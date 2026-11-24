"""End-to-end deterministic design pipeline: Circuit IR -> KiCad project.

Each step is recorded with status, timing and outputs in `run.json`, so the
UI and the AI can report exactly what happened (and what did not).
"""
from __future__ import annotations

import csv
import shutil
import json
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..kicad.cli.runner import Cancelled, current_cancel
from ..kicad.detection import KiCadCapabilities
from ..kicad.export.exporter import ExportRecord, Exporter, step_to_glb
from ..kicad.library import LibraryManager
from ..kicad.pcb.client import PcbWorker
from ..kicad.schematic.netlist import compare, export_netlist
from ..kicad.schematic.writer import SchematicWriter
from .circuit.model import Circuit, NetKind
from .placement.heuristic import PlacementError, footprint_geom, place
from .routing.freerouting import route
from .validation.drc import ValidationStatus, parse_json_report, parse_report
from .validation.erc import apply_waivers
from .validation.repair import plan as repair_plan


@dataclass
class Step:
    name: str
    status: str = "pending"        # pending / ok / failed / skipped
    seconds: float = 0.0
    detail: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class RunRecord:
    project: str
    kicad_version: str | None
    steps: list[Step] = field(default_factory=list)
    validation_status: str = ValidationStatus.not_validated.value
    summary: dict = field(default_factory=dict)

    def save(self, path: Path):
        path.write_text(json.dumps(asdict(self), indent=2, default=str))


class StepFailed(RuntimeError):
    pass


MAX_REPAIR_ITERATIONS = 4
MAX_RELAYOUTS = 2
MAX_GROWS = 1          # auto-sized boards may grow once (~10 %) if routing still cannot finish
SPACING_FIXABLE = {"silk_overlap", "courtyards_overlap", "silk_over_copper", "clearance"}


class DesignPipeline:
    def __init__(self, caps: KiCadCapabilities, freerouting_jar: Path | None, routing_profile: str = "standard"):
        if not caps.installed or not caps.python_path:
            raise RuntimeError("KiCad with Python bindings is required for the PCB pipeline")
        self.caps = caps
        self.libs = LibraryManager(caps.symbol_dir, caps.footprint_dir, caps.models3d_dir)
        self.worker = PcbWorker(caps.python_path, caps.path_env())
        self.exporter = Exporter(caps.cli_path, caps.path_env())
        self.jar = freerouting_jar
        self.routing_profile = routing_profile

    def run(self, circuit: Circuit, project_dir: Path, *, exports: bool = True,
            on_event=None, keep: dict | None = None) -> RunRecord:
        """Run the full design pipeline. `on_event(dict)` receives step_start/step_end events
        (called from the worker thread); a CancelToken in runner.current_cancel stops the run
        between steps and kills any running subprocess."""
        d = project_dir.resolve()
        d.mkdir(parents=True, exist_ok=True)
        rec = RunRecord(circuit.name, self.caps.version)
        ctx: dict = {"keep": keep or {}}
        self._ctx = ctx
        emit = on_event or (lambda e: None)
        token = current_cancel.get()

        def step(name, fn, required=True):
            if token:
                token.check()
            s = Step(name)
            rec.steps.append(s)
            emit({"type": "step_start", "name": name})
            t = time.monotonic()
            try:
                s.detail = fn() or {}
                s.status = "ok"
            except Cancelled:
                s.status, s.error = "cancelled", "cancelled by user"
                s.seconds = round(time.monotonic() - t, 2)
                rec.save(d / "run.json")
                emit({"type": "step_end", "name": name, "status": s.status, "seconds": s.seconds})
                raise
            except Exception as e:
                s.status, s.error = "failed", f"{type(e).__name__}: {e}"
                s.detail["trace"] = traceback.format_exc()[-1500:]
                if required:
                    s.seconds = round(time.monotonic() - t, 2)
                    rec.save(d / "run.json")
                    emit({"type": "step_end", "name": name, "status": s.status, "seconds": s.seconds,
                          "error": s.error})
                    raise StepFailed(f"{name}: {s.error}") from e
            s.seconds = round(time.monotonic() - t, 2)
            rec.save(d / "run.json")
            emit({"type": "step_end", "name": name, "status": s.status, "seconds": s.seconds, "error": s.error})
            return s

        pcb_path = d / f"{circuit.name}.kicad_pcb"
        job_base = dict(output=str(pcb_path), board=circuit.board.model_dump(),
                        rules=circuit.rules.model_dump())

        # 1. schematic --------------------------------------------------------
        def s_schematic():
            r = SchematicWriter(self.libs).write(circuit, d)
            if r.problems:
                raise ValueError("; ".join(r.problems))
            ctx["sch"] = r
            self._write_lib_tables(circuit, d, r.footprints)
            return {"file": str(r.path), "sheets": [str(f) for f in r.files], "footprints": r.footprints}
        step("schematic", s_schematic)

        # 2. verify connectivity via KiCad's own netlister ---------------------
        def s_netlist():
            nl = export_netlist(self.caps.cli_path, ctx["sch"].path, d / f"{circuit.name}.net")
            rep = compare(circuit.connections(), nl)
            if not rep.ok:
                raise ValueError(f"schematic connectivity mismatch: {rep.missing + rep.extra}")
            ctx["netlist"], ctx["netmap"] = nl, rep.net_name_map
            return {"kicad_nets": {k: sorted(map(list, v)) for k, v in nl.nets.items()},
                    "ir_to_kicad": rep.net_name_map}
        step("netlist_verification", s_netlist)

        # 2b. electrical rules check (kicad-cli, KiCad >= 8) -------------------
        if self.caps.cli_erc:
            def s_erc():
                r = apply_waivers(self.exporter.erc(ctx["sch"].path, d / "reports"), circuit)
                ctx["erc"] = r
                if not r["ran"]:
                    raise RuntimeError(f"ERC produced no report (rc={r['rc']})")
                return r
            step("erc", s_erc, required=False)

        # 3-7. layout (place -> build -> planes -> route -> pours), then DRC + controlled repair
        repairs: list[dict] = []
        routed = {"value": False}
        state = {"spacing": 1.0, "layout": 0}
        history: dict[str, set] = {}

        def layout():
            state["layout"] += 1
            tag = f"#L{state['layout']}"
            step(f"placement{tag}", lambda: self._place(circuit, ctx, state["spacing"]))
            step(f"pcb_build{tag}", lambda: self._build(circuit, ctx, job_base))
            planes = [z for z in self._zones(circuit, ctx) if z["plane"]]
            if planes:
                step(f"planes{tag}", lambda: self.worker.call("add-zones", dict(job_base, zones=planes)))
                widths = {ctx["netmap"].get(n.name, n.name): n.track_width_mm for n in circuit.nets
                          if n.track_width_mm}
                step(f"plane_fanout{tag}", lambda: self.worker.call("plane-fanout", dict(
                    job_base, plane_nets=sorted({z["net"] for z in planes}), widths=widths)))
            net_widths = self._net_widths(circuit, ctx)
            if net_widths:
                step(f"neck_down{tag}", lambda: self.worker.call("neck-stubs", dict(
                    job_base, net_widths=net_widths, plane_nets=sorted({z["net"] for z in planes}))),
                     required=False)
            if any("USB_C_Receptacle" in (c.footprint or "") for c in circuit.components):
                step(f"usb_fanout{tag}", lambda: self.worker.call("usbc-fanout", job_base), required=False)
            routed["value"] = False
            routed.pop("narrowed", None)
            pours = [z for z in self._zones(circuit, ctx) if not z["plane"]]
            routed["pours"] = pours
            step(f"routing{tag}", lambda: self._route(circuit, d, job_base, routed), required=False)
            if pours:
                step(f"copper_pours{tag}", lambda: self.worker.call("add-zones", dict(job_base, zones=pours)),
                     required=False)
            step(f"labels{tag}", lambda: self.worker.call("place-labels", job_base), required=False)
            history.clear()

        layout()
        relayouts = grows = 0
        for it in range(1, MAX_REPAIR_ITERATIONS + MAX_RELAYOUTS + MAX_GROWS + 2):
            tag = f"#{it}"

            def s_drc():
                if self.caps.cli_drc and (self.caps.major or 0) >= 9:
                    # KiCad 9/10: pcbnew.WriteDRCReport needs the GUI app object and crashes
                    # from a script (assert "process" failed in Pgm()); the CLI is the supported path.
                    rpt = d / "reports" / f"drc{tag}.json"
                    rpt.parent.mkdir(exist_ok=True)
                    self.exporter.drc(pcb_path, rpt, refill_zones=True)
                    res = parse_json_report(rpt)
                else:
                    rpt = d / "reports" / f"drc{tag}.rpt"
                    rpt.parent.mkdir(exist_ok=True)
                    self.worker.call("drc", dict(job_base, drc_report=str(rpt)))
                    res = parse_report(rpt)
                ctx["drc"] = res
                return res.to_dict()
            step(f"drc{tag}", s_drc, required=False)
            drc = ctx.get("drc")
            if drc is None or it > MAX_REPAIR_ITERATIONS + MAX_RELAYOUTS + MAX_GROWS:
                break
            rp = repair_plan(drc, history)
            if not routed["value"]:
                rp.relayout.append("routing_incomplete")
            relayout_ok = bool(rp.relayout) and relayouts < MAX_RELAYOUTS and not state.get("no_room")
            if relayout_ok:
                relayouts += 1
                new_spacing = state["spacing"] + 0.5
                try:
                    self._place(circuit, ctx, new_spacing)       # dry run: does it still fit?
                except PlacementError as e:
                    repairs.append({"iteration": it, "kind": "relayout", "trigger": rp.relayout,
                                    "status": "skipped", "reason": f"more spacing does not fit: {e}"})
                    state["no_room"] = True
                    relayout_ok = False                         # still apply the targeted repairs below
            if relayout_ok:
                state["spacing"] = new_spacing
                repairs.append({"iteration": it, "kind": "relayout", "trigger": rp.relayout,
                                "action": f"re-place with spacing {new_spacing:.2f} mm and re-route"})
                layout()
                continue
            if rp.relayout and circuit.board.auto_size and grows < MAX_GROWS:
                # the size was the tool's choice ("smallest that fits"): give routing more room
                grows += 1
                w0, h0 = circuit.board.width_mm, circuit.board.height_mm
                self._grow_board(circuit, job_base)
                state["spacing"] = 1.0
                state.pop("no_room", None)
                ctx["keep"] = {}                       # previous positions assume the old outline
                repairs.append({"iteration": it, "kind": "grow_board", "trigger": rp.relayout,
                                "action": f"board {w0:g}x{h0:g} -> {circuit.board.width_mm:g}x"
                                          f"{circuit.board.height_mm:g} mm (size was automatic) and re-route"})
                ctx["board_grown"] = [w0, h0, circuit.board.width_mm, circuit.board.height_mm]
                layout()
                continue
            if rp.ops:
                res = step(f"repair{tag}", lambda: self.worker.call("repair", dict(job_base, ops=rp.ops)),
                           required=False)
                repairs.append({"iteration": it, "kind": "targeted", "ops": rp.ops,
                                "status": res.status, "unfixable": rp.unfixable})
                continue
            if rp.unfixable:
                repairs.append({"iteration": it, "kind": "none", "unfixable": rp.unfixable})
            break
        ctx["repairs"] = repairs

        stats = self.worker.call("stats", job_base)
        ctx["stats"] = stats
        ctx["decoupling"] = self._decoupling_check(circuit, stats)

        # 8. exports -----------------------------------------------------------
        if exports:
            def s_exports():
                recs: list[ExportRecord] = []
                recs += self.exporter.pcb_svgs(pcb_path, d / "renders")
                lay = self.exporter.layer_svgs(pcb_path, d / "renders" / "layers", circuit.board.layers)
                recs.append(lay)
                if lay.ok:
                    self._write_board_json(d / "renders" / "layers", ctx["stats"], circuit)
                recs += self.exporter.schematic(ctx["sch"].path, d / "renders")
                recs += self.exporter.fabrication(pcb_path, d / "fab", circuit.board.layers)
                st = self.exporter.step(pcb_path, d / "3d")
                recs.append(st)
                if self.caps.cli_glb:
                    g = self.exporter.glb(pcb_path, d / "3d")
                    if not g.ok and st.ok:          # fall back to our STEP converter
                        g = step_to_glb(Path(st.files[0]), d / "3d" / f"{circuit.name}.glb")
                    recs.append(g)
                elif st.ok:
                    recs.append(step_to_glb(Path(st.files[0]), d / "3d" / f"{circuit.name}.glb"))
                if self.caps.cli_ipc2581:
                    recs.append(self.exporter.ipc2581(pcb_path, d / "fab"))
                recs.append(self._bom(ctx["netlist"], circuit, d / "fab" / f"{circuit.name}-bom.csv"))
                failed = [r.name for r in recs if not r.ok]
                missing3d = sorted(ref for ref, fp in ctx["sch"].footprints.items()
                                   if not self.libs.resolve_footprint(fp).has_3d_model)
                return {"records": [asdict(r) for r in recs], "failed": failed,
                        "components_without_3d_model": missing3d}
            step("exports", s_exports, required=False)

        # trace widths vs. the widths the design requires (KiCad DRC does not check current)
        try:
            ctx["widths"] = self.worker.call("width-audit", dict(job_base, net_widths=self._net_widths(circuit, ctx)))
            # Required widths carry a 1.5x current margin (IPC-2221: width ~ I^1.38, so the width
            # for the actual current is about required / 1.75). Narrower than required but still
            # wide enough for the actual current = reduced margin (warning), not a failure.
            br = ctx["widths"].get("below_required") or {}
            reduced = {n: v for n, v in br.items() if v["narrowest"] >= v["required"] / 1.75}
            for n in reduced:
                br.pop(n)
            ctx["widths"]["reduced_margin"] = reduced
        except Exception as e:                                   # report, never hide
            ctx["widths"] = {"error": str(e)}

        # functional design checks (ratings, rails, LEDs, I2C, boot pins) --------------
        try:
            from .validation.functional import check as functional_check
            ctx["functional"] = functional_check(circuit)
        except Exception as e:                                   # report, never hide
            ctx["functional"] = {"error": str(e)[:300]}

        # validation status from what actually ran -------------------------------
        drc = ctx.get("drc")
        if drc is None:
            rec.validation_status = ValidationStatus.unavailable.value
        elif drc.errors == 0 and drc.unconnected == 0 and drc.footprint_errors == 0:
            # "validated" only when ERC actually ran clean too; otherwise DRC-only = partial.
            erc = ctx.get("erc")
            erc_clean = bool(erc and erc["ran"] and erc["counts"].get("error", 0) == 0)
            rec.validation_status = (ValidationStatus.validated.value if erc_clean
                                     else ValidationStatus.partial.value)
            if (ctx.get("widths") or {}).get("below_required"):
                rec.validation_status = ValidationStatus.failed.value   # under-width current paths
            if (ctx.get("functional") or {}).get("errors"):
                rec.validation_status = ValidationStatus.failed.value   # the circuit itself has a fault
        else:
            rec.validation_status = ValidationStatus.failed.value
        rec.summary = {
            "routing_complete": routed["value"], "narrowed_rails": routed.get("narrowed"),
            "board_grown": ctx.get("board_grown"),
            "trace_widths": ctx.get("widths"),
            "functional": ctx.get("functional"),
            "decoupling": ctx.get("decoupling"),
            "tracks": stats["tracks"], "vias": stats["vias"],
            "track_length_mm": stats["track_length_mm"], "unrouted": stats["unrouted_connections"],
            "drc": None if drc is None else {"violations": drc.violations, "unconnected": drc.unconnected,
                                             "footprint_errors": drc.footprint_errors,
                                             "errors": drc.errors, "warnings": drc.warnings},
            "repairs": ctx.get("repairs", []),
            "unrouted_connections": [] if drc is None else [
                " <-> ".join(i.items) for i in drc.issues if i.kind == "unconnected_items"],
            "recommendations": self._recommend(circuit, drc, routed["value"]),
            "erc": None if not ctx.get("erc") else {"counts": ctx["erc"]["counts"], "types": ctx["erc"]["types"],
                                                    "waived": ctx["erc"].get("waived", []),
                                                    "info": ctx["erc"].get("info", [])},
            "validation_scope": ["DRC (KiCad engine)", "schematic netlist == IR",
                                 "design checks (datasheet limits, rails, LEDs, I2C, boot pins)"] +
                                (["ERC (KiCad engine)"] if ctx.get("erc", {}).get("ran") else
                                 ["ERC NOT run: unavailable in this KiCad CLI"]),
        }
        # 9. order package (after validation, so its README states the real result) --------
        if exports and (d / "fab" / "gerbers").is_dir():
            try:
                from .fab.package import build as build_package
                try:
                    facts = self.worker.call("fab-facts", job_base, timeout=120)
                except Exception as fe:                      # fall back to what layout stats know
                    facts = self._facts_from_stats(ctx.get("stats") or {}, circuit, str(fe))
                src = None
                if getattr(self, "lookup_parts", True):
                    from .fab.sourcing import Sourcing
                    from ..core.config import data_dir
                    src = Sourcing(data_dir() / "cache" / "jlc_parts.json")
                pkg = build_package(circuit.name, circuit, d / "fab", facts, {
                    "status": rec.validation_status,
                    "drc": rec.summary["drc"], "erc": rec.summary["erc"],
                    "design_checks": {k: (rec.summary.get("functional") or {}).get(k) for k in ("errors", "warnings")},
                    "routing_complete": routed["value"]}, sourcing=src)
                rec.summary["order_package"] = {"folder": str(pkg.folder), "files": pkg.files,
                                                "sourcing": pkg.sourcing,
                                                "needs_sourcing": pkg.needs_sourcing,
                                                "capability_notes": pkg.capability_notes,
                                                "detail": pkg.detail}
            except Exception as e:                               # report, never hide
                rec.summary["order_package"] = {"error": str(e)[:400]}
        rec.save(d / "run.json")
        return rec


    # ------------------------------------------------------------- helpers
    @staticmethod
    def _grow_board(circuit, job_base):
        """About 10 % larger in each direction (5 mm steps); corner mounting holes move with
        their corners."""
        import math as _m
        b = circuit.board
        w, h = b.width_mm, b.height_mm
        nw, nh = 5 * _m.ceil(w * 1.1 / 5), 5 * _m.ceil(h * 1.1 / 5)
        for mh in b.mounting_holes:
            if mh.x_mm > w / 2:
                mh.x_mm += nw - w
            if mh.y_mm > h / 2:
                mh.y_mm += nh - h
        b.width_mm, b.height_mm = float(nw), float(nh)
        job_base["board"] = b.model_dump()

    @staticmethod
    def _facts_from_stats(stats: dict, circuit, why: str) -> dict:
        """Order-form facts without the fab-facts worker: dimensions, layers and part
        counts are known; drill and track minimums are reported as unknown."""
        parts = {"top": {"smd": 0, "tht": 0}, "bottom": {"smd": 0, "tht": 0}}
        tht = []
        for f in stats.get("footprints", []):
            pg = f.get("pad_geom") or []
            if not pg:
                continue
            side = "bottom" if f.get("layer") == "B.Cu" else "top"
            is_tht = any(p.get("through") and p.get("net") for p in pg)
            parts[side]["tht" if is_tht else "smd"] += 1
            if is_tht:
                tht.append(f["ref"])
        b = stats.get("board") or {}
        return {"width_mm": circuit.board.width_mm, "height_mm": circuit.board.height_mm,
                "copper_layers": b.get("copper_layers", circuit.board.layers),
                "thickness_mm": b.get("thickness_mm", 1.6), "min_track_mm": None, "min_clearance_mm":
                circuit.rules.clearance_mm, "min_via_diameter_mm": None, "min_via_drill_mm": None,
                "min_pth_drill_mm": None, "min_npth_drill_mm": None, "min_annular_ring_mm": None,
                "vias": stats.get("vias", 0), "parts": parts, "tht_refs": tht, "pth_drill_by_ref": {},
                "note": f"measured values unavailable ({why[:120]})"}

    def _write_lib_tables(self, circuit: Circuit, d: Path, footprints: dict[str, str]):
        var = f"KICAD{self.caps.major}"
        fp_libs = sorted({fp.split(":")[0] for fp in footprints.values()} |
                         {h.footprint.split(":")[0] for h in circuit.board.mounting_holes})
        sym_libs = sorted({c.symbol.split(":")[0] for c in circuit.components} | {"power"})
        fp = ["(fp_lib_table", "  (version 7)"] + [
            f'  (lib (name "{l}")(type "KiCad")(uri "${{{var}_FOOTPRINT_DIR}}/{l}.pretty")(options "")(descr ""))'
            for l in fp_libs] + [")"]
        sy = ["(sym_lib_table", "  (version 7)"] + [
            f'  (lib (name "{l}")(type "KiCad")(uri "${{{var}_SYMBOL_DIR}}/{l}.kicad_sym")(options "")(descr ""))'
            for l in sym_libs] + [")"]
        (d / "fp-lib-table").write_text("\n".join(fp) + "\n", encoding="utf-8")
        (d / "sym-lib-table").write_text("\n".join(sy) + "\n", encoding="utf-8")

    @staticmethod
    def _write_board_json(layer_dir: Path, stats: dict, circuit: Circuit):
        """Board data for the interactive 2D viewer, in the SVG page's coordinate frame.
        The offset comes from where KiCad drew the board outline (board origin at 0,0)."""
        import re as _re
        txt = (layer_dir / "layer_Edge_Cuts.svg").read_text()
        m = _re.search(r'd="M\s*([-\d.]+),([-\d.]+)', txt)
        off = [float(m.group(1)), float(m.group(2))] if m else [0.0, 0.0]
        vb = _re.search(r'viewBox="([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+)"', txt)
        data = {"offset_mm": off, "page_mm": [float(vb.group(3)), float(vb.group(4))] if vb else None,
                "board": stats.get("board"), "width_mm": circuit.board.width_mm, "height_mm": circuit.board.height_mm,
                "layers": circuit.board.layers,
                "footprints": [{k: f[k] for k in ("ref", "value", "fpid", "x", "y", "rotation", "layer", "bbox", "pad_geom")}
                               for f in stats["footprints"]],
                "descriptions": {c.ref: c.description for c in circuit.components},
                "blocks": {c.ref: c.block for c in circuit.components}}
        (layer_dir / "board.json").write_text(json.dumps(data))

    @staticmethod
    def _recommend(circuit, drc, routed: bool) -> list[str]:
        """Options for the user when automation could not finish. Never applied automatically:
        layer count and board size are cost/mechanical decisions that need approval."""
        out = []
        if not routed or (drc and drc.unconnected):
            if circuit.board.layers == 2:
                out.append("Move to 4 layers with inner GND/power planes (needs your approval: cost).")
            out.append(f"Enlarge the board beyond {circuit.board.width_mm:g} x {circuit.board.height_mm:g} mm "
                       "or move connectors to reduce congestion.")
            out.append("Route the listed connection manually in KiCad; everything else is preserved.")
        if drc and drc.errors and routed and not drc.unconnected:
            out.append("Review the remaining DRC errors in the Validation tab; they were not auto-fixable.")
        return out

    @staticmethod
    def _zones(circuit, ctx) -> list[dict]:
        nm = ctx["netmap"]
        if circuit.board.zones:
            return [{"net": nm.get(z.net, z.net), "layer": z.layer, "plane": z.plane} for z in circuit.board.zones]
        if circuit.board.ground_pour:
            gnd = [nm[n.name] for n in circuit.nets if n.kind == NetKind.ground and n.name in nm]
            if gnd:
                return [{"net": gnd[0], "layer": l, "plane": False} for l in ("F.Cu", "B.Cu")]
        return []

    DECOUPLING_OK_MM, DECOUPLING_FAR_MM = 3.0, 5.0

    def _decoupling_check(self, circuit, stats) -> list[dict]:
        """Distance from each decoupling capacitor's supply pad to the nearest supply pin of
        the chip it serves (same block first). Short is what keeps supply noise down; KiCad's
        DRC has no notion of this, so it is measured here and reported."""
        import math
        import re as _re
        pads = {f["ref"]: f.get("pad_geom", []) for f in stats.get("footprints", [])}
        comps = {c.ref: c for c in circuit.components}
        out = []
        loop = _re.compile(r"buck input|buck output|bootstrap|buck inductor|LDO input|LDO output|charger|VM bulk", _re.I)
        for c in circuit.components:
            is_dec = c.ref.startswith("C") and _re.search(r"decoupl|\bHF\b|bypass", c.description, _re.I)
            is_loop = c.ref[0] in "CL" and loop.search(c.description)
            if not (is_dec or is_loop):
                continue
            nets = [n for n in c.pin_nets.values() if n != "GND"]
            supply = [n for n in nets if circuit.net(n).kind.value == "power"] or nets
            if c.ref.startswith("L"):                  # an inductor belongs at the switch node
                supply = [n for n in nets if circuit.net(n).kind.value != "power"] or supply
            if not supply:
                continue
            net = supply[0]
            ics = [u for u in circuit.components if u.ref.startswith("U") and net in u.pin_nets.values()]
            if is_loop and _re.search(r"output", c.description, _re.I):
                # output capacitors belong at the inductor's output, not at the feedback pin
                ics = [u for u in circuit.components if u.ref.startswith("L") and u.block == c.block
                       and net in u.pin_nets.values()] or ics
            same = [u for u in ics if u.block and u.block == c.block]
            if is_loop and not is_dec and not same:
                continue
            best = None
            for u in (same or ics):
                for cp in pads.get(c.ref, []):
                    if not cp["net"].endswith(net):
                        continue
                    for up in pads.get(u.ref, []):
                        if up["net"].endswith(net):
                            dd = math.hypot(cp["x"] - up["x"], cp["y"] - up["y"])
                            if best is None or dd < best[0]:
                                best = (dd, u.ref)
            if best is None:
                continue
            d_mm = round(best[0], 2)
            out.append({"cap": c.ref, "chip": best[1], "net": net, "distance_mm": d_mm,
                        "role": "regulator loop" if is_loop and not is_dec else "decoupling",
                        "status": _dist_status(d_mm, self.DECOUPLING_OK_MM, self.DECOUPLING_FAR_MM,
                                               output=bool(is_loop and _re.search("output", c.description, _re.I)))})
        return sorted(out, key=lambda r: -r["distance_mm"])

    @staticmethod
    def _net_widths(circuit, ctx) -> dict:
        """Required trace width per net, wherever it is wider than the default signal width.
        An explicit width from the design (current-based) always wins; other power nets use
        the power width; low-current logic rails the default width (IPC-2221 for <= 0.75 A)."""
        from .routing.freerouting import LOGIC_RAILS
        nm, r = ctx["netmap"], circuit.rules
        out = {}
        for n in circuit.nets:
            if n.name not in nm:
                continue
            if n.track_width_mm:
                w = n.track_width_mm
            elif n.kind != NetKind.signal:
                # logic rails carry <= 0.75 A with margin: the default width meets IPC-2221
                w = r.track_width_mm if n.name in LOGIC_RAILS else r.power_track_width_mm
            else:
                continue
            if w > r.track_width_mm:
                out[nm[n.name]] = w
        return out

    def _place(self, circuit, ctx, spacing):
        from .placement import heuristic as _h
        geoms = {ref: footprint_geom(self.libs.resolve_footprint(fp).path, fp)
                 for ref, fp in ctx["sch"].footprints.items()}
        # kept blocks only on the first layout; a re-layout (more spacing) starts afresh
        keep = ctx.get("keep") if abs(spacing - 1.0) < 1e-6 else None
        ctx["placement"] = place(circuit, geoms, spacing=spacing, keep=keep)
        return {"spacing_mm": spacing, "kept_blocks": _h.DEBUG.get("kept_blocks", []),
                "positions": {p.ref: [p.x, p.y, p.rotation] for p in ctx["placement"]}}

    def _build(self, circuit, ctx, job_base):
        nm = ctx["netmap"]
        fps = []
        for p in ctx["placement"]:
            comp = next(c for c in circuit.components if c.ref == p.ref)
            fpid = ctx["sch"].footprints[p.ref]
            lib_path, name = self.libs.footprint_lib_path(fpid)
            fps.append(dict(ref=p.ref, value=comp.value, lib=fpid.split(":")[0], name=name,
                            lib_path=lib_path, x=p.x, y=p.y, rotation=p.rotation,
                            sym_path=ctx["sch"].symbol_paths[p.ref],
                            pads={pin: nm[n] for pin, n in comp.pin_nets.items()}))
        for i, h in enumerate(circuit.board.mounting_holes, 1):
            lib_path, name = self.libs.footprint_lib_path(h.footprint)
            fps.append(dict(ref=f"H{i}", value="MountingHole", lib=h.footprint.split(":")[0],
                            name=name, lib_path=lib_path, x=h.x_mm, y=h.y_mm, hide_reference=True))
        # One netclass per required width, so every net is routed at the width the proposal
        # states (load outputs, input rail, logic rail...), not just a single "power" width.
        groups: dict[float, list[str]] = {}
        for net, w in self._net_widths(circuit, ctx).items():
            groups.setdefault(round(w, 3), []).append(net)
        classes = [{"name": "Power" if w == round(circuit.rules.power_track_width_mm, 3) else
                    f"W{w:g}mm".replace(".", "p"), "track_width_mm": w, "nets": sorted(nets)}
                   for w, nets in sorted(groups.items())]
        usb = sorted(nm[n] for n in ("USB_D+", "USB_D-") if n in nm)
        if usb:      # escape from 0.5 mm-pitch USB-C pins and join the A/B rows: thin track, small vias
            classes.append({"name": "USB", "track_width_mm": 0.2, "via_diameter_mm": 0.6, "via_drill_mm": 0.3,
                            "nets": usb})
        job = dict(job_base, nets=[{"name": v} for v in nm.values()], footprints=fps, netclasses=classes)
        return self.worker.call("build", job)

    def _route(self, circuit, d, job_base, routed):
        """Attempt 1 routes from scratch. Later attempts are completion passes: the DSN is
        re-exported from the board as routed so far, so Freerouting keeps the existing copper
        and only has to finish (ripping up locally) the connections still missing."""
        if not self.jar or not self.jar.exists():
            raise FileNotFoundError("Freerouting jar not configured")
        dsn, ses = d / f"{circuit.name}.dsn", d / f"{circuit.name}.ses"
        planes = [z for z in self._zones(circuit, self._ctx) if z["plane"]]
        detail = {}
        n_parts = len(circuit.components)
        # Freerouting results vary run to run, so a *fresh* full route is a genuinely new try;
        # a completion pass on a stuck result rarely moves it.
        plan = [("full", 40), ("completion", 40), ("full", 40), ("completion", 40)]
        for attempt, (mode, passes) in enumerate(plan, 1):
            if mode == "full" and attempt > 1:
                self.worker.call("clear-routes", job_base)
            self.worker.call("export-dsn", dict(job_base, dsn=str(dsn)))
            # completion passes get a hard cap: Freerouting 1.9 can hang loading pre-routed DSNs
            rr = route(self.jar, dsn, ses, max_passes=passes,
                       optimize="off" if self.routing_profile == "fast" else "standard",
                       timeout=(300 + 6 * n_parts) if mode == "full" else 240)
            a = detail[f"attempt_{attempt}"] = {"mode": mode,
                                                "ok": rr.ok, "max_passes": passes, "error": rr.error,
                                                "freerouting_s": round(rr.seconds, 1)}
            if not rr.ok:
                a["log_tail"] = rr.log_tail[-5:]
                continue
            a["import"] = self.worker.call("import-ses", dict(job_base, ses=str(ses)))
            a["cleanup"] = self.worker.call("cleanup-tracks", dict(
                job_base, plane_nets=sorted({z["net"] for z in planes}),
                plane_layers=sorted({z["layer"] for z in planes})))
            st = self.worker.call("stats", job_base)
            a["unrouted"] = st["unrouted_connections"]
            if st["unrouted_connections"] and routed.get("pours"):
                # Ground pins reached by the copper pour (e.g. through neck-down stubs) show as
                # unrouted until the pour exists: check on a filled copy before retrying.
                a["unrouted"] = st["unrouted_connections"] = self._unrouted_with_pours(job_base, routed["pours"])
                a["checked_with_pours"] = True
            if st["unrouted_connections"] == 0:
                routed["value"] = True
                break
            best = detail.get("best")
            if best is None or st["unrouted_connections"] < best["unrouted"]:
                detail["best"] = {"attempt": attempt, "unrouted": st["unrouted_connections"]}
                shutil.copyfile(job_base["output"], job_base["output"] + ".best")
        best_file = Path(job_base["output"] + ".best")
        if not routed["value"] and best_file.exists():
            shutil.copyfile(best_file, job_base["output"])       # keep the best attempt, not the last
        if not routed["value"]:
            self._narrow_completion(circuit, dsn, ses, job_base, planes, detail, routed)
        detail["complete"] = routed["value"]
        best_file.unlink(missing_ok=True)
        if not routed["value"]:
            raise RuntimeError(f"routing incomplete: {detail}")
        return detail

    def _unrouted_with_pours(self, job_base, pours) -> int:
        tmp = job_base["output"] + ".pourcheck.kicad_pcb"
        shutil.copyfile(job_base["output"], tmp)
        try:
            self.worker.call("add-zones", dict(job_base, output=tmp, zones=pours))
            return self.worker.call("stats", dict(job_base, output=tmp))["unrouted_connections"]
        finally:
            Path(tmp).unlink(missing_ok=True)

    def _narrow_completion(self, circuit, dsn, ses, job_base, planes, detail, routed):
        """Last fallback: low-current logic rails (+3V3 etc., <= ~1 A here) may use 0.3 mm
        instead of the 0.5 mm power width to squeeze through the last gap. Input, load and
        ground nets are never narrowed. Kept only if it reduces the unrouted count; the
        narrowed width is reported in the summary."""
        from .routing.freerouting import LOGIC_RAILS, narrow_dsn_class
        rails = [n.name for n in circuit.nets if n.name in LOGIC_RAILS]
        if not rails or circuit.rules.power_track_width_mm <= 0.3:
            return
        before = self.worker.call("stats", job_base)["unrouted_connections"]
        if routed.get("pours"):
            before = self._unrouted_with_pours(job_base, routed["pours"])
        shutil.copyfile(job_base["output"], job_base["output"] + ".pre_narrow")
        self.worker.call("export-dsn", dict(job_base, dsn=str(dsn)))
        moved = narrow_dsn_class(dsn, rails, 300)
        a = detail["attempt_narrow"] = {"mode": "completion", "narrowed_nets": moved, "width_mm": 0.3}
        if not moved:
            return
        rr = route(self.jar, dsn, ses, max_passes=40,
                   optimize="off" if self.routing_profile == "fast" else "standard", timeout=240)
        a.update(ok=rr.ok, error=rr.error, freerouting_s=round(rr.seconds, 1))
        if rr.ok:
            a["import"] = self.worker.call("import-ses", dict(job_base, ses=str(ses)))
            a["cleanup"] = self.worker.call("cleanup-tracks", dict(
                job_base, plane_nets=sorted({z["net"] for z in planes}),
                plane_layers=sorted({z["layer"] for z in planes})))
            after = self.worker.call("stats", job_base)["unrouted_connections"]
            if after and routed.get("pours"):
                after = self._unrouted_with_pours(job_base, routed["pours"])
            a["unrouted"] = after
            if after < before:
                routed["value"] = after == 0
                detail["narrowed"] = routed["narrowed"] = {"nets": moved, "width_mm": 0.3}
                Path(job_base["output"] + ".pre_narrow").unlink(missing_ok=True)
                return
        shutil.copyfile(job_base["output"] + ".pre_narrow", job_base["output"])   # no gain: undo
        Path(job_base["output"] + ".pre_narrow").unlink(missing_ok=True)

    @staticmethod
    def _bom(netlist, circuit: Circuit, path: Path) -> ExportRecord:
        groups: dict[tuple, list[str]] = {}
        for ref, c in sorted(netlist.components.items()):
            groups.setdefault((c["value"], c["footprint"]), []).append(ref)
        desc = {c.ref: c.description for c in circuit.components}
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["References", "Qty", "Value", "Footprint", "Description"])
            for (val, fp), refs in groups.items():
                w.writerow([" ".join(refs), len(refs), val, fp, desc.get(refs[0], "")])
        return ExportRecord("bom", True, [str(path)], f"{len(groups)} lines from KiCad netlist")


def _dist_status(d_mm: float, ok_mm: float, far_mm: float, output: bool = False) -> str:
    """Output capacitors of a regulator sit at the inductor's output; their loop is less
    critical than the input loop, so they get a wider band."""
    if output:
        ok_mm, far_mm = ok_mm + 2.0, far_mm + 3.0
    return "ok" if d_mm <= ok_mm else "far" if d_mm > far_mm else "acceptable"
