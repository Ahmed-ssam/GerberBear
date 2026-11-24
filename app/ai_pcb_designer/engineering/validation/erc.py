"""ERC post-processing: datasheet-intended connections and generator artefacts.

KiCad's pin-type matrix is generic. A few connections our blocks make on purpose trip it
even though the datasheet asks for exactly that wiring (e.g. the CH340 V3 pin, typed
"power output", must be tied to VCC when the chip runs from 3.3 V). Each such pin is
declared on the component (Component.erc_waive: pin -> reason); a violation is waived only
when one of its items is a declared pin. Waived items stay listed in the report with their
reason, they just do not count as errors or warnings.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_PIN = re.compile(r"Symbol (\S+) Pin (\S+)")
# Our schematics embed library symbols flattened (derived symbols resolved against their
# parent). KiCad compares that copy textually with the library and may report a mismatch
# although pins and graphics are identical; it says nothing about the circuit.
INFO_TYPES = {"lib_symbol_mismatch"}


def apply_waivers(erc: dict, circuit) -> dict:
    if not erc or not erc.get("ran"):
        return erc
    path = Path(erc["report"])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return erc
    waive = {(c.ref, str(p)): why for c in circuit.components for p, why in (c.erc_waive or {}).items()}
    counts = {"error": 0, "warning": 0}
    types: dict[str, int] = {}
    waived, info = [], []
    for sheet in data.get("sheets", []):
        for v in sheet.get("violations", []):
            items = [it.get("description", "") for it in v.get("items", [])]
            reason = None
            for d in items:
                m = _PIN.search(d)
                if m and (m.group(1), m.group(2)) in waive:
                    reason = waive[(m.group(1), m.group(2))]
                    break
            t = v.get("type", "?")
            if reason:
                waived.append({"type": t, "items": items, "reason": reason})
                continue
            if t in INFO_TYPES:
                info.append({"type": t, "items": items})
                continue
            sev = v.get("severity", "error")
            counts[sev] = counts.get(sev, 0) + 1
            types[t] = types.get(t, 0) + 1
    return {**erc, "counts": counts, "types": types, "waived": waived, "info": info,
            "raw_counts": erc.get("counts")}
