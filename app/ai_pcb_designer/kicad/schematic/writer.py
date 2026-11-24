"""Deterministic KiCad schematic (.kicad_sch) generation from a Circuit.

- Components with `sheet` set go into child sheets (one file per sheet) under a
  root sheet; otherwise everything is on the root sheet.
- Connectivity: every connected pin gets a short wire stub ending in a net
  label (local if the net stays on one sheet, global if it crosses sheets) or
  a power symbol. Unused pins get no-connect flags. Stacked pins are handled.
- Layout: each functional block is a group of cells with a caption; groups are
  shelf-packed and the smallest paper (A4..A0) that fits is chosen.
Verified by exporting KiCad's own netlist and comparing it to the IR.
"""
from __future__ import annotations

import datetime as _dt
import math
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ...engineering.circuit.model import Circuit, Component, NetKind
from .. import sexpr
from ..library import LibraryManager, ResolvedSymbol, verify_pin_pad_mapping
from ..sexpr import Sym

SCH_VERSION = 20230121          # KiCad 7 schematic format
GRID = 2.54
STUB = 2.54
PAPERS = [("A4", 297, 210), ("A3", 420, 297), ("A2", 594, 420), ("A1", 841, 594), ("A0", 1189, 841)]
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 25.4, 25.4, 45.0   # bottom keeps the title block clear


def _u() -> str:
    return str(uuid.uuid4())


def _snap(v: float) -> float:
    return round(round(v / GRID) * GRID, 4)


def _effects(hide=False, justify=None, size=1.27):
    e = [Sym("effects"), [Sym("font"), [Sym("size"), size, size]]]
    if justify:
        e.append([Sym("justify"), *[Sym(j) for j in justify]])
    if hide:
        e.append(Sym("hide"))
    return e


def _prop(name, value, x, y, hide=False, justify=None, angle=0):
    return [Sym("property"), name, value, [Sym("at"), x, y, angle], _effects(hide, justify)]


def sheet_filename(circuit_name: str, sheet: str) -> str:
    return f"{circuit_name}-{re.sub(r'[^A-Za-z0-9]+', '_', sheet).strip('_').lower()}.kicad_sch"


@dataclass
class SchematicResult:
    path: Path                              # root schematic
    symbol_paths: dict[str, str]            # ref -> KIID path for PCB footprint linking ("/sheet/sym")
    root_uuid: str
    footprints: dict[str, str]              # ref -> resolved footprint lib id
    files: list[Path] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


class _Sheet:
    """Accumulates items for one schematic file."""

    def __init__(self, writer: "SchematicWriter", project: str, instance_path: str):
        self.w = writer
        self.project = project
        self.instance_path = instance_path      # e.g. "/<root>" or "/<root>/<sheet>"
        self.items: list = []
        self.lib_symbols: dict[str, list] = {}

    def place_symbol(self, rs: ResolvedSymbol, ref, value, x, y, fp="", ds="~", angle=0,
                     extra_props=None, show_ref=True, show_value=True) -> str:
        self.lib_symbols.setdefault(rs.lib_id, rs.node)
        uid = _u()
        minx, miny, maxx, maxy = rs.bbox
        node = [Sym("symbol"), [Sym("lib_id"), rs.lib_id], [Sym("at"), x, y, angle],
                [Sym("unit"), 1], [Sym("in_bom"), Sym("no" if rs.is_power else "yes")],
                [Sym("on_board"), Sym("no" if rs.is_power else "yes")],
                [Sym("dnp"), Sym("no")], [Sym("uuid"), uid],
                _prop("Reference", ref, round(x + maxx + 1.27, 2), round(y - maxy - 1.27, 2),
                      hide=not show_ref, justify=["left"]),
                _prop("Value", value, *self.w._value_pos(rs, x, y, angle), hide=not show_value,
                      justify=None if rs.is_power else ["left"]),
                _prop("Footprint", fp, x, y, hide=True),
                _prop("Datasheet", ds or "~", x, y, hide=True)]
        for k, v in (extra_props or {}).items():
            node.append(_prop(k, v, x, y, hide=True))
        for p in rs.pins:
            node.append([Sym("pin"), p.number, [Sym("uuid"), _u()]])
        node.append([Sym("instances"), [Sym("project"), self.project,
                     [Sym("path"), self.instance_path, [Sym("reference"), ref], [Sym("unit"), 1]]]])
        self.items.append(node)
        return uid

    def wire(self, x1, y1, x2, y2):
        self.items.append([Sym("wire"), [Sym("pts"), [Sym("xy"), x1, y1], [Sym("xy"), x2, y2]],
                           [Sym("stroke"), [Sym("width"), 0], [Sym("type"), Sym("default")]],
                           [Sym("uuid"), _u()]])

    def label(self, name, x, y, ang, is_global):
        just = ["left", "bottom"] if ang in (0, 90) else ["right", "bottom"]
        if is_global:
            just = ["left"] if ang in (0, 90) else ["right"]
            self.items.append([Sym("global_label"), name, [Sym("shape"), Sym("passive")],
                               [Sym("at"), x, y, ang], [Sym("fields_autoplaced")], _effects(justify=just),
                               [Sym("uuid"), _u()],
                               [Sym("property"), "Intersheetrefs", "${INTERSHEET_REFS}",
                                [Sym("at"), x, y, 0], _effects(hide=True)]])
        else:
            self.items.append([Sym("label"), name, [Sym("at"), x, y, ang], [Sym("fields_autoplaced")],
                               _effects(justify=just), [Sym("uuid"), _u()]])

    def text(self, s, x, y, size=2.0):
        self.items.append([Sym("text"), s, [Sym("at"), x, y, 0],
                           _effects(justify=["left", "bottom"], size=size), [Sym("uuid"), _u()]])

    def power_symbol(self, lib_id, x, y, angle=0):
        rs = self.w.libs.resolve_symbol(lib_id)
        self.w._pwr += 1
        ref = f"#FLG{self.w._pwr:03d}" if "PWR_FLAG" in lib_id else f"#PWR{self.w._pwr:03d}"
        self.place_symbol(rs, ref, rs.properties.get("Value", lib_id.split(":")[1]), x, y, angle=angle,
                          show_ref=False, show_value="PWR_FLAG" not in lib_id)

    def document(self, uid, paper, title, rev, sheet_instances: bool, page_title=None) -> list:
        doc = [Sym("kicad_sch"), [Sym("version"), SCH_VERSION], [Sym("generator"), Sym("ai_pcb_designer")],
               [Sym("uuid"), uid], [Sym("paper"), paper],
               [Sym("title_block"), [Sym("title"), page_title or title],
                [Sym("date"), _dt.date.today().isoformat()], [Sym("rev"), rev],
                [Sym("comment"), 1, "Generated by AI PCB Designer"]],
               [Sym("lib_symbols"), *self.lib_symbols.values()], *self.items]
        if sheet_instances:
            doc.append([Sym("sheet_instances"), [Sym("path"), "/", [Sym("page"), "1"]]])
        return doc


class SchematicWriter:
    def __init__(self, libs: LibraryManager):
        self.libs = libs
        self._pwr = 0
        self._flag_at: dict = {}

    # screen direction of a power symbol's body (y down) for each rotation, given its natural direction
    _ROT = {0: lambda d: d, 90: lambda d: (d[1], -d[0]), 180: lambda d: (-d[0], -d[1]), 270: lambda d: (-d[1], d[0])}

    def _power_angle(self, lib_id: str, dx: int, dy: int) -> int:
        nat = self._body_dir(self.libs.resolve_symbol(lib_id))
        for ang, f in self._ROT.items():
            if f(nat) == (dx, dy):
                return ang
        return 0

    @staticmethod
    def _body_dir(rs: ResolvedSymbol) -> tuple[int, int]:
        # a power symbol's single pin points from the connection point toward its body
        a = math.radians(rs.pins[0].angle)
        return (round(math.cos(a)), -round(math.sin(a)))

    @classmethod
    def _value_pos(cls, rs: ResolvedSymbol, x, y, angle):
        minx, miny, maxx, maxy = rs.bbox
        if not rs.is_power:
            return round(x + maxx + 1.27, 2), round(y - miny + 2.54, 2)
        bx, by = cls._ROT[int(angle) % 360](cls._body_dir(rs))
        return round(x + bx * 6.0, 2), round(y + by * 5.08, 2)

    def _choose_flag_pins(self, circuit: Circuit, resolved: dict) -> dict:
        """One PWR_FLAG per net that has power-input pins but nothing that drives it
        (no power_out pin), as KiCad's ERC requires. Each flag is attached to a real pin on
        that net, preferring two-pin parts (caps, resistors) where the stub has free space
        beside it. Isolated power-symbol + flag pairs are flagged as unconnected by
        KiCad 9+, so flags are never placed on their own."""
        types: dict[str, set] = {}
        cands: dict[str, list] = {}
        for comp in circuit.components:
            rs = resolved[comp.ref]
            for num, net in comp.pin_nets.items():
                et = rs.pin(num).etype
                types.setdefault(net, set()).add(et)
                cands.setdefault(net, []).append((len(comp.pin_nets), comp.ref, num))
        out = {}
        for net, ets in types.items():
            # power symbols carry a (hidden) power_in pin, so every power/ground net counts
            powered = "power_in" in ets or circuit.net(net).kind != NetKind.signal
            if powered and "power_out" not in ets:
                n_pins, ref, num = sorted(cands[net], key=lambda c: (c[0] > 2, c[0], c[1], c[2]))[0]
                out[(ref, num)] = net
        return out

    # ------------------------------------------------------------------ layout
    def _cell(self, rs: ResolvedSymbol, comp: Component, circuit: Circuit):
        """Footprint of a symbol plus its stubs/labels, as (w, h, dx, dy) where (dx,dy) is the
        symbol origin relative to the cell's top-left corner."""
        minx, miny, maxx, maxy = rs.bbox
        lab = {d: 0.0 for d in ("l", "r", "t", "b")}
        for p in rs.pins:
            net = comp.pin_nets.get(p.number)
            side = {0: "l", 180: "r", 90: "b", 270: "t"}[int(p.angle) % 360]
            ext = STUB + (7.0 if net and circuit.net(net).kind != NetKind.signal else
                          (len(net) * 1.05 + 3.0 if net else 1.5))
            if (comp.ref, p.number) in self._flag_at:
                ext += STUB + 2.0
            lab[side] = max(lab[side], ext)
        w = (maxx - minx) + lab["l"] + lab["r"] + 6
        h = (maxy - miny) + max(lab["t"], 4) + max(lab["b"], 6) + 4
        return w, h, lab["l"] + 3 - minx, max(lab["t"], 4) + 2 + maxy

    def _layout(self, groups, page_w):
        """Shelf-pack block groups; returns positions and used height."""
        x, y, shelf_h = MARGIN_X, MARGIN_TOP, 0.0
        out = []
        for name, cells in groups:
            # cells of a block flow in rows of at most ~120 mm
            gx, gy, row_h, gw, rows = 0.0, 6.0, 0.0, 0.0, []
            for cell in cells:
                if gx > 0 and gx + cell[1] > max(120.0, cell[1]):
                    gx, gy, row_h = 0.0, gy + row_h, 0.0
                rows.append((cell, gx, gy))
                gx += cell[1]
                gw = max(gw, gx)
                row_h = max(row_h, cell[2])
            gh = gy + row_h + 4
            if x > MARGIN_X and x + gw > page_w - MARGIN_X:
                x, y, shelf_h = MARGIN_X, y + shelf_h, 0.0
            out.append((name, x, y, rows))
            x += gw + 8
            shelf_h = max(shelf_h, gh)
        return out, y + shelf_h

    # ------------------------------------------------------------------- write
    def write(self, circuit: Circuit, out_dir: Path) -> SchematicResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        self._pwr = 0
        root_uuid = _u()
        project = circuit.name
        problems: list[str] = []
        footprints: dict[str, str] = {}
        sym_paths: dict[str, str] = {}

        sheets: dict[str | None, list[Component]] = {}
        for c in circuit.components:
            sheets.setdefault(c.sheet, []).append(c)
        hierarchical = any(k is not None for k in sheets)
        net_sheets: dict[str, set] = {}
        for c in circuit.components:
            for n in c.pin_nets.values():
                net_sheets.setdefault(n, set()).add(c.sheet)

        resolved: dict[str, ResolvedSymbol] = {}
        for comp in circuit.components:
            rs = self.libs.resolve_symbol(comp.symbol)
            resolved[comp.ref] = rs
            fp_id = comp.footprint or rs.properties.get("Footprint", "")
            if not fp_id:
                problems.append(f"{comp.ref}: no footprint given and symbol has no default")
            else:
                problems += [f"{comp.ref}: {m}" for m in verify_pin_pad_mapping(rs, self.libs.resolve_footprint(fp_id))]
            footprints[comp.ref] = fp_id
            for pin in comp.pin_nets:
                rs.pin(pin)  # raises LibraryError for a non-existent pin
        self._flag_at = self._choose_flag_pins(circuit, resolved)

        root = _Sheet(self, project, f"/{root_uuid}")
        files: list[Path] = []
        sheet_nodes = []
        page = 1

        def emit(sheet: _Sheet, comps: list[Component], path_prefix: str, page_title: str):
            groups: dict[str, list] = {}
            for comp in comps:
                rs = resolved[comp.ref]
                w, h, dx, dy = self._cell(rs, comp, circuit)
                groups.setdefault(comp.block or comp.ref, []).append((comp, w, h, dx, dy))
            group_list = list(groups.items())
            reserve = 0 if hierarchical else 25.0          # room for PWR_FLAGs on a flat sheet
            for paper, pw, ph in PAPERS:
                placed, used_h = self._layout(group_list, pw)
                if used_h + reserve <= ph - MARGIN_BOTTOM or paper == "A0":
                    break
            if used_h > ph - MARGIN_BOTTOM:
                problems.append(f"sheet {page_title!r} overflows A0; split it into more sheets")
            for name, gx, gy, rows in placed:
                if len(groups) > 1 or hierarchical:
                    sheet.text(name, _snap(gx), _snap(gy + 3))
                for (comp, w, h, dx, dy), cx, cy in rows:
                    sx, sy = _snap(gx + cx + dx), _snap(gy + cy + dy)
                    self._place_component(sheet, circuit, comp, resolved[comp.ref], sx, sy, footprints,
                                          sym_paths, path_prefix, net_sheets, hierarchical, problems)
            return paper, used_h

        if hierarchical:
            for idx, (name, comps) in enumerate(sorted(sheets.items(), key=lambda kv: (kv[0] is None, kv[0] or ""))):
                if name is None:
                    emit(root, comps, "", "root")   # parts without a sheet live on the root
                    continue
                page += 1
                suid = _u()
                child = _Sheet(self, project, f"/{root_uuid}/{suid}")
                paper, _ = emit(child, comps, f"/{suid}", name)
                fname = sheet_filename(circuit.name, name)
                fpath = out_dir / fname
                fpath.write_text(sexpr.dumps(child.document(_u(), paper, circuit.title or circuit.name,
                                                            circuit.revision, False,
                                                            page_title=f"{circuit.title or circuit.name} — {name}"))
                                 + "\n", encoding="utf-8")
                files.append(fpath)
                n = page - 2
                sx, sy = 30.48 + (n % 4) * 63.5, 38.1 + (n // 4) * 33.02
                sheet_nodes.append([Sym("sheet"), [Sym("at"), sx, sy], [Sym("size"), 50.8, 20.32],
                                    [Sym("fields_autoplaced")],
                                    [Sym("stroke"), [Sym("width"), 0.1524], [Sym("type"), Sym("solid")]],
                                    [Sym("fill"), [Sym("color"), 0, 0, 0, 0.0]], [Sym("uuid"), suid],
                                    [Sym("property"), "Sheetname", name, [Sym("at"), sx, round(sy - 0.7, 2), 0],
                                     _effects(justify=["left", "bottom"])],
                                    [Sym("property"), "Sheetfile", fname, [Sym("at"), sx, round(sy + 20.92, 2), 0],
                                     _effects(justify=["left", "top"])],
                                    [Sym("instances"), [Sym("project"), project,
                                                        [Sym("path"), f"/{root_uuid}", [Sym("page"), str(page)]]]]])
            root.items.extend(sheet_nodes)
            root_paper = "A4" if len(sheet_nodes) <= 16 else "A3"
            fy = _snap(38.1 + math.ceil(len(sheet_nodes) / 4) * 33.02 + 15.24)
        else:
            root_paper, used_h = emit(root, sheets.get(None, []), "", "root")
            fy = _snap(used_h + 10)

        path = out_dir / f"{circuit.name}.kicad_sch"
        doc = root.document(root_uuid, root_paper, circuit.title or circuit.name, circuit.revision, True)
        path.write_text(sexpr.dumps(doc) + "\n", encoding="utf-8")
        return SchematicResult(path, sym_paths, root_uuid, footprints, [path, *files], problems)

    def _place_component(self, sheet: _Sheet, circuit: Circuit, comp: Component, rs: ResolvedSymbol,
                         sx, sy, footprints, sym_paths, path_prefix, net_sheets, hierarchical, problems):
        uid = sheet.place_symbol(rs, comp.ref, comp.value, sx, sy, fp=footprints[comp.ref],
                                 ds=comp.datasheet or rs.properties.get("Datasheet", "~"),
                                 extra_props={"Description": comp.description} if comp.description else None)
        sym_paths[comp.ref] = f"{path_prefix}/{uid}"
        seen: dict[tuple, str | None] = {}
        # connected pins first so a stacked unused pin never gets a no-connect over a live pin
        for p in sorted(rs.pins, key=lambda p: comp.pin_nets.get(p.number) is None):
            px, py = round(sx + p.x, 4), round(sy - p.y, 4)        # lib Y-up -> sheet Y-down
            net_name = comp.pin_nets.get(p.number)
            key = (px, py)
            if key in seen:
                if net_name is not None and seen[key] is not None and seen[key] != net_name:
                    problems.append(f"{comp.ref}: stacked pins at one point on nets {seen[key]} and {net_name}")
                continue
            seen[key] = net_name
            if net_name is None:
                sheet.items.append([Sym("no_connect"), [Sym("at"), px, py], [Sym("uuid"), _u()]])
                continue
            out = math.radians(p.angle + 180)
            dx, dy = round(math.cos(out)), -round(math.sin(out))
            flagged = (comp.ref, p.number) in self._flag_at
            stub = STUB * 2 if flagged else STUB
            ex, ey = round(px + dx * stub, 4), round(py + dy * stub, 4)
            if not flagged:
                sheet.wire(px, py, ex, ey)
            else:
                # PWR_FLAG where two wire segments meet: KiCad connects a pin only at a wire
                # END, never partway along one. Body sideways, clear of the stub-end symbol.
                fx, fy = round(px + dx * STUB, 4), round(py + dy * STUB, 4)
                sheet.wire(px, py, fx, fy)
                sheet.wire(fx, fy, ex, ey)
                body = (abs(dy), -abs(dx))
                sheet.power_symbol("power:PWR_FLAG", fx, fy, angle=self._power_angle("power:PWR_FLAG", *body))
            net = circuit.net(net_name)
            if net.kind == NetKind.signal:
                ang = {(1, 0): 0, (-1, 0): 180, (0, -1): 90, (0, 1): 270}[(dx, dy)]
                sheet.label(net.name, ex, ey, ang, is_global=hierarchical and len(net_sheets[net.name]) > 1)
            else:
                # Rotate the power symbol so its body points straight out along the stub.
                # Never bend the wire sideways: adjacent pins sit one grid pitch away.
                sheet.power_symbol(net.power_symbol, ex, ey, angle=self._power_angle(net.power_symbol, dx, dy))
