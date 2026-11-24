"""Order-ready fabrication and assembly packages.

Everything here is derived from files KiCad wrote and from the copper measured on the
board. Nothing is invented: a part without a supplier number stays blank in the BOM and is
listed as needing sourcing, and the README states the validation result as it is.

Output (in <project>/fab/order/):
  <name>_gerbers.zip      Gerbers (Protel extensions, X2), PTH/NPTH Excellon drill, job file
  <name>_BOM_JLCPCB.csv   Comment, Designator, Footprint, LCSC Part #
  <name>_CPL_JLCPCB.csv   Designator, Mid X, Mid Y, Layer, Rotation
  <name>_BOM.csv          generic BOM for PCBWay and other assemblers (manufacturer + MPN)
  ORDER_README.txt        the order form values and what to check before paying
  order_spec.json         the same facts for tools
"""
from __future__ import annotations

import csv
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# Common board-house capabilities (standard service, 1 oz outer copper). Used only to flag
# boards that would need an advanced (more expensive) process - not to change the design.
STANDARD_MIN = {"track_mm": 0.127, "via_drill_mm": 0.3, "pth_drill_mm": 0.3,
                "annular_ring_mm": 0.13, "via_diameter_mm": 0.5}

GERBER_ORDER = ["F_Cu", "In1_Cu", "In2_Cu", "B_Cu", "F_Mask", "B_Mask", "F_Silkscreen", "B_Silkscreen",
                "F_Paste", "B_Paste", "Edge_Cuts"]


@dataclass
class Package:
    folder: Path
    files: list[str] = field(default_factory=list)
    needs_sourcing: list[str] = field(default_factory=list)   # BOM lines without a supplier number
    sourcing: dict = field(default_factory=dict)                # catalogue lookup summary
    capability_notes: list[str] = field(default_factory=list)
    ok: bool = True
    detail: str = ""


def _read_pos(pos_csv: Path) -> list[dict]:
    with pos_csv.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


def build(name: str, circuit, fab_dir: Path, facts: dict, validation: dict, sourcing=None) -> Package:
    """`facts` comes from the worker's fab-facts command, `validation` from the run summary
    (status, drc, erc). Raises if the KiCad outputs it packages are missing."""
    gerbers = fab_dir / "gerbers"
    pos_csv = fab_dir / f"{name}-pos.csv"
    out = fab_dir / "order"
    out.mkdir(parents=True, exist_ok=True)
    pkg = Package(out)

    # 1. gerber zip: flat, every layer + drill + job file ------------------------------
    files = sorted(p for p in gerbers.iterdir() if p.is_file() and not p.name.endswith("drl_map.gbr"))
    have = {p.stem.split(f"{name}-", 1)[-1] for p in files}
    need = ["F_Cu", "B_Cu", "F_Mask", "B_Mask", "F_Silkscreen", "Edge_Cuts"] + \
           [f"In{k}_Cu" for k in range(1, facts["copper_layers"] - 1)]
    missing = [l for l in need if l not in have]
    drills = [p for p in files if p.suffix == ".drl"]
    if missing or not drills:
        raise RuntimeError(f"cannot build the Gerber zip: missing {missing + ([] if drills else ['drill'])}")
    zpath = out / f"{name}_gerbers.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.name)
    pkg.files.append(str(zpath))

    # 2. BOM + CPL ---------------------------------------------------------------------
    comps = {c.ref: c for c in circuit.components}
    rows = [r for r in _read_pos(pos_csv) if r["Ref"] in comps and not comps[r["Ref"]].dnp]
    groups: dict[tuple, list[str]] = {}
    for r in rows:
        c = comps[r["Ref"]]
        groups.setdefault((c.value, r["Package"], c.lcsc or "", c.manufacturer or "", c.mpn or ""), []).append(r["Ref"])
    found: dict[tuple, dict] = {}
    if sourcing is not None:                       # live catalogue lookup, confirmed matches only
        for key, refs in groups.items():
            if key[2]:
                continue
            c = comps[refs[0]]
            hit = sourcing.find(c.symbol, c.value, c.footprint or "")
            if hit:
                found[key] = hit
        sourcing.save()
        pkg.sourcing = {"matched": len(found), "errors": sourcing.errors[:3],
                        "extended": sum(1 for h in found.values() if not h["basic"]),
                        "date": __import__("time").strftime("%Y-%m-%d")}

    def natural(ref):
        i = next((k for k, ch in enumerate(ref) if ch.isdigit()), len(ref))
        return (ref[:i], int(ref[i:] or 0))

    bom_j = out / f"{name}_BOM_JLCPCB.csv"
    bom_g = out / f"{name}_BOM.csv"
    with bom_j.open("w", newline="", encoding="utf-8") as fj, bom_g.open("w", newline="", encoding="utf-8") as fg:
        wj, wg = csv.writer(fj), csv.writer(fg)
        wj.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        wg.writerow(["Item", "Designator", "Qty", "Manufacturer", "Manufacturer Part Number",
                     "Value", "Package", "Type", "Description", "Supplier Part (LCSC)", "Part number source"])
        for i, ((val, fp, lcsc, mfr, mpn), refs) in enumerate(sorted(groups.items(),
                                                                         key=lambda kv: natural(sorted(kv[1], key=natural)[0])), 1):
            refs = sorted(refs, key=natural)
            hit = found.get((val, fp, lcsc, mfr, mpn))
            src = "design" if lcsc or mpn else ""
            if hit:
                lcsc, mpn = hit["lcsc"], mpn or (hit.get("mfr") or "")
                src = (f"JLCPCB catalogue match {pkg.sourcing['date']} ({'basic' if hit['basic'] else 'extended'}"
                       f"): {str(hit.get('description') or '')[:80]}")
            wj.writerow([val, ",".join(refs), fp, lcsc])
            tht = any(_is_tht(r, facts) for r in refs)
            wg.writerow([i, ",".join(refs), len(refs), mfr, mpn, val, fp, "THT" if tht else "SMD",
                         comps[refs[0]].description, lcsc, src])
            if not lcsc and not mpn:
                pkg.needs_sourcing.append(f"{','.join(refs)}  {val}  ({fp})")
    cpl = out / f"{name}_CPL_JLCPCB.csv"
    with cpl.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for r in sorted(rows, key=lambda r: natural(r["Ref"])):
            w.writerow([r["Ref"], _fmt(float(r["PosX"])) + "mm", _fmt(float(r["PosY"])) + "mm",
                        "Bottom" if r["Side"].lower() == "bottom" else "Top", _fmt(float(r["Rot"]) % 360)])
    pkg.files += [str(bom_j), str(cpl), str(bom_g)]

    # 3. capability check against a standard process ----------------------------------
    chk = [("min_track_mm", "track_mm", "narrowest track"),
           ("min_via_drill_mm", "via_drill_mm", "smallest via drill"),
           ("min_pth_drill_mm", "pth_drill_mm", "smallest plated hole"),
           ("min_annular_ring_mm", "annular_ring_mm", "smallest annular ring"),
           ("min_via_diameter_mm", "via_diameter_mm", "smallest via pad")]
    for fk, sk, label in chk:
        v = facts.get(fk)
        if v is not None and v < STANDARD_MIN[sk] - 1e-6:
            where = ""
            if fk == "min_pth_drill_mm":
                refs = sorted(r for r, d in facts.get("pth_drill_by_ref", {}).items() if d < STANDARD_MIN[sk] - 1e-6)
                if refs:
                    where = f" (in the footprint of {', '.join(refs)}, from the KiCad library)"
            pkg.capability_notes.append(f"{label} {v} mm{where} is below the common standard "
                                        f"{STANDARD_MIN[sk]} mm: choose the small-hole option or confirm "
                                        "with the board house")

    # 4. README + spec -----------------------------------------------------------------
    spec = _spec(name, facts, validation, pkg)
    (out / "order_spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    (out / "ORDER_README.txt").write_text(_readme(name, spec, pkg), encoding="utf-8", newline="\r\n")
    pkg.files += [str(out / "ORDER_README.txt"), str(out / "order_spec.json")]
    pkg.detail = (f"{len(groups)} BOM lines, {len(rows)} placements, "
                  f"{len(pkg.needs_sourcing)} lines need sourcing")
    return pkg


def _is_tht(ref: str, facts: dict) -> bool:
    return ref in set(facts.get("tht_refs", []))


def _spec(name, f, validation, pkg) -> dict:
    p = f["parts"]
    return {
        "board": name,
        "layers": f["copper_layers"],
        "dimensions_mm": [f["width_mm"], f["height_mm"]],
        "thickness_mm": f["thickness_mm"],
        "outer_copper_oz": 1,
        "min_track_mm": f["min_track_mm"],
        "min_clearance_mm": f.get("min_clearance_mm"),
        "min_via_diameter_mm": f["min_via_diameter_mm"],
        "min_via_drill_mm": f["min_via_drill_mm"],
        "min_pth_drill_mm": f["min_pth_drill_mm"],
        "min_annular_ring_mm": f["min_annular_ring_mm"],
        "vias": f["vias"],
        "assembly": {"top_smd": p["top"]["smd"], "top_tht": p["top"]["tht"],
                     "bottom_smd": p["bottom"]["smd"], "bottom_tht": p["bottom"]["tht"],
                     "sides": "both" if (p["bottom"]["smd"] + p["bottom"]["tht"]) else "top"},
        "validation": validation,
        "needs_sourcing": pkg.needs_sourcing,
        "sourcing": pkg.sourcing,
        "capability_notes": pkg.capability_notes,
    }


def _readme(name, s, pkg) -> str:
    v = s["validation"]
    status = v.get("status", "unknown")
    a = s["assembly"]
    L = []
    L.append(f"{name} - fabrication and assembly package")
    L.append("=" * len(L[0]))
    L.append("")
    if status == "partially_validated":
        L.append("Validation: KiCad DRC 0 errors, 0 unconnected. KiCad ERC was NOT run by this KiCad")
        L.append("version - open the schematic in KiCad and run Inspect > Electrical Rules Checker before ordering.")
        L.append("")
    elif status != "validated":
        L.append(f"!! VALIDATION STATUS: {status.upper()} - do not order until the issues in the app's")
        L.append("!! Checks view are resolved. These files describe the board exactly as it is now.")
        L.append("")
    else:
        L.append("Validation: KiCad DRC 0 errors, 0 unconnected; KiCad ERC 0 errors; trace widths meet")
        L.append("the current each net carries; design checks (datasheet voltage limits, capacitor ratings,")
        L.append("LED currents, I2C addresses, ESP32 boot pins) found no errors. No circuit simulation was run.")
        L.append("")
    L.append("FILES")
    L.append(f"  {name}_gerbers.zip      upload this as the PCB (Gerber RS-274X2 + Excellon drill)")
    L.append(f"  {name}_BOM_JLCPCB.csv   JLCPCB assembly: bill of materials")
    L.append(f"  {name}_CPL_JLCPCB.csv   JLCPCB assembly: pick-and-place (centroid) file")
    L.append(f"  {name}_BOM.csv          other assemblers (PCBWay, ...): BOM with manufacturer part numbers")
    L.append("  order_spec.json         the values below, machine-readable")
    L.append("")
    L.append("ORDER FORM VALUES")
    L.append(f"  Layers ................ {s['layers']}")
    L.append(f"  Dimensions ............ {s['dimensions_mm'][0]:g} x {s['dimensions_mm'][1]:g} mm")
    L.append(f"  PCB thickness ......... {s['thickness_mm']:g} mm")
    L.append(f"  Outer copper .......... {s['outer_copper_oz']} oz (trace widths were sized for 1 oz)")
    if s["layers"] > 2:
        L.append("  Inner copper .......... 0.5 oz or 1 oz (planes; either is fine)")
        L.append("  Layer stack ........... F_Cu / In1_Cu / In2_Cu / B_Cu (file names say which is which)")
    L.append(f"  Min track / spacing ... {s['min_track_mm'] or 'see DRC rules'} mm / {s['min_clearance_mm']} mm")
    if s["min_via_drill_mm"]:
        L.append(f"  Min via ............... {s['min_via_diameter_mm']} mm pad, {s['min_via_drill_mm']} mm drill"
                 f" ({s['vias']} vias)")
    if s["min_pth_drill_mm"]:
        L.append(f"  Min plated hole ....... {s['min_pth_drill_mm']} mm")
    L.append("  Surface finish ........ HASL lead-free or ENIG (ENIG for fine-pitch parts)")
    L.append("  Solder mask / silk .... any colour")
    L.append("  Remove order number ... your choice (no spot is reserved on the silkscreen)")
    L.append("")
    L.append("ASSEMBLY")
    L.append(f"  Sides ................. {a['sides']}   (top: {a['top_smd']} SMD, {a['top_tht']} THT; "
             f"bottom: {a['bottom_smd']} SMD, {a['bottom_tht']} THT)")
    L.append("  Through-hole parts are in the BOM; many assemblers solder them by hand for a fee,")
    L.append("  or exclude them and solder them yourself.")
    L.append("")
    sc = s.get("sourcing") or {}
    if sc.get("matched"):
        L.append(f"SUPPLIER PART NUMBERS: {sc['matched']} BOM lines matched in the JLCPCB catalogue on {sc['date']}")
        L.append("  Accepted only when the catalogue description confirms value, rating and package (see the")
        L.append(f"  'Part number source' column of the generic BOM). {sc.get('extended', 0)} are 'extended' parts")
        L.append("  (JLCPCB charges a loading fee per extended part). Stock changes daily: the assembler's BOM")
        L.append("  check at upload time is the final word.")
        L.append("")
    elif sc.get("errors"):
        L.append(f"SUPPLIER PART NUMBERS: catalogue lookup was not possible ({sc['errors'][0]}).")
        L.append("")
    if pkg.needs_sourcing:
        L.append(f"PARTS WITHOUT A SUPPLIER NUMBER ({len(pkg.needs_sourcing)} BOM lines)")
        L.append("  No confirmed match was found, and the app never guesses part numbers. Pick these in the")
        L.append("  assembler's parts search (match value, package and voltage/tolerance):")
        L += [f"    - {x}" for x in pkg.needs_sourcing]
        L.append("")
    if pkg.capability_notes:
        L.append("PROCESS NOTES")
        L += [f"  - {x}" for x in pkg.capability_notes]
        L.append("")
    L.append("BEFORE YOU PAY")
    L.append("  1. Open the Gerber zip in the board house's viewer: check the outline, all layers, the holes.")
    L.append("  2. In the assembly preview, check the orientation of every polarised part: diodes, LEDs,")
    L.append("     electrolytic capacitors and pin 1 of every IC. Rotations are KiCad's (IPC-7351 zero")
    L.append("     orientation); assemblers sometimes use a different zero for a package and rotate it")
    L.append("     in their preview - fix it there, and the engineer review will confirm.")
    L.append("  3. Confirm every part's value and package against the BOM.")
    L.append("")
    return "\n".join(L) + "\n"
