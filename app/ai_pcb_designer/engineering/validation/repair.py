"""Map DRC issues to controlled repair operations.

Every operation is a named, typed edit with an explicit target; nothing here
invents geometry. Issues without a safe operation are reported, not "fixed".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .drc import DrcResult

_REF = re.compile(r"Reference '([^']+)'")
_PAD = re.compile(r"(?:PTH |NPTH )?[Pp]ad (\S+) (?:\[[^\]]*\] )?of (\S+)")
_FP = re.compile(r"Footprint (\S+)")
_TRACK = re.compile(r"@\(([-\d.]+) mm, ([-\d.]+) mm\): Track \[([^\]]*)\] on (\S+), length ([\d.]+) mm")
_VIA = re.compile(r"@\(([-\d.]+) mm, ([-\d.]+) mm\): Via \[([^\]]*)\]")

SILK = {"silk_overlap", "silk_over_copper", "silk_edge_clearance"}
RELAYOUT = {"courtyards_overlap", "clearance", "hole_clearance", "copper_edge_clearance",
            "unconnected_items", "shorting_items", "tracks_crossing", "hole_near_hole"}


@dataclass
class RepairPlan:
    ops: list[dict] = field(default_factory=list)      # targeted, in-place
    relayout: list[str] = field(default_factory=list)  # issue kinds that need re-placement/re-route
    unfixable: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.ops and not self.relayout


def plan(drc: DrcResult, history: dict[str, set]) -> RepairPlan:
    """`history` remembers which refs/pads were already treated so escalation is monotonic."""
    p = RepairPlan()
    shrink, hide, solid = set(), set(), set()
    for issue in drc.issues:
        text = " ".join(issue.items)
        if issue.kind in SILK:
            for ref in _REF.findall(text):
                if ref in history.setdefault("hidden", set()):
                    continue
                (hide if ref in history.setdefault("shrunk", set()) else shrink).add(ref)
            if not _REF.findall(text):
                p.unfixable.append(f"{issue.kind}: {issue.message[:120]}")
        elif issue.kind == "starved_thermal":
            for pad, ref in _PAD.findall(text):
                if (ref, pad) not in history.setdefault("solid", set()):
                    solid.add((ref, pad))
        elif issue.kind in ("track_dangling", "via_dangling"):
            hits = [{"kind": "track", "x": float(x), "y": float(y), "net": n, "layer": l, "length": float(ln)}
                    for x, y, n, l, ln in _TRACK.findall(text)] + \
                   [{"kind": "via", "x": float(x), "y": float(y), "net": n} for x, y, n in _VIA.findall(text)]
            for h in hits:
                key = (h["kind"], round(h["x"], 3), round(h["y"], 3))
                if key not in history.setdefault("removed", set()):
                    history["removed"].add(key)
                    p.ops.append({"op": "remove_dangling", **h})
            if not hits:
                p.unfixable.append(f"{issue.kind}: {issue.message[:120]}")
        elif issue.kind in RELAYOUT:
            p.relayout.append(issue.kind)
        elif issue.severity != "warning" or issue.kind not in ("lib_footprint_issues",):
            p.unfixable.append(f"{issue.kind} ({issue.severity}): {issue.message[:120]}")
    if shrink:
        p.ops.append({"op": "shrink_reference", "refs": sorted(shrink), "size_mm": 0.8})
        history["shrunk"] |= shrink
    if hide:
        p.ops.append({"op": "hide_reference", "refs": sorted(hide)})
        history["hidden"] |= hide
    if solid:
        p.ops.append({"op": "solid_zone_connection", "pads": sorted(solid)})
        history["solid"] |= solid
    p.relayout = sorted(set(p.relayout))
    return p
