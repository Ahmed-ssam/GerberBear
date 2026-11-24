"""Parse KiCad's DRC text report into structured results.

The report is produced by KiCad's DRC engine (pcbnew.WriteDRCReport on 7.x,
`kicad-cli pcb drc` on 8+). Nothing here is inferred: counts come from the file.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path


class ValidationStatus(str, Enum):
    not_validated = "not_validated"
    in_progress = "in_progress"
    validated = "validated"
    failed = "validation_failed"
    partial = "partially_validated"
    unavailable = "validation_unavailable"


@dataclass
class DrcIssue:
    kind: str              # e.g. "clearance", "unconnected_items"
    severity: str          # error / warning
    message: str
    items: list[str] = field(default_factory=list)
    section: str = "violations"


@dataclass
class DrcResult:
    report_path: str
    violations: int
    unconnected: int
    footprint_errors: int
    issues: list[DrcIssue]

    @property
    def errors(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    def to_dict(self):
        d = asdict(self)
        d.update(errors=self.errors, warnings=self.warnings)
        return d


_HEAD = re.compile(r"^\[(?P<kind>[a-z_]+)\]:\s*(?P<msg>.*)$")
_SEV = re.compile(r"Severity:\s*(error|warning|exclusion|ignore)", re.I)
_COUNT = re.compile(r"\*\*\s*Found\s+(\d+)\s+(DRC violations|unconnected pads|Footprint errors)", re.I)


def parse_report(path: Path) -> DrcResult:
    text = path.read_text(encoding="utf-8", errors="replace")
    counts = {"DRC violations": 0, "unconnected pads": 0, "Footprint errors": 0}
    issues: list[DrcIssue] = []
    section = "violations"
    cur: DrcIssue | None = None
    for line in text.splitlines():
        m = _COUNT.search(line)
        if m:
            key = next(k for k in counts if k.lower() == m.group(2).lower())
            counts[key] = int(m.group(1))
            section = {"DRC violations": "violations", "unconnected pads": "unconnected",
                       "Footprint errors": "footprint"}[key]
            continue
        h = _HEAD.match(line.strip())
        if h:
            cur = DrcIssue(h.group("kind"), "error", h.group("msg").strip(), section=section)
            issues.append(cur)
            continue
        if cur and line.strip():
            s = _SEV.search(line)
            if s:
                cur.severity = s.group(1).lower()
                cur.message += f" ({line.strip()})"
            elif line.strip().startswith("@("):
                cur.items.append(line.strip())
    return DrcResult(str(path), counts["DRC violations"], counts["unconnected pads"],
                     counts["Footprint errors"], issues)


def parse_json_report(path: Path) -> DrcResult:
    """Parse `kicad-cli pcb drc --format json` (KiCad >= 8). Items are rendered in the same
    "@(x mm, y mm): description" form as the text report so repair rules work on both.
    Raises if the file does not look like a DRC report: a missing section must never be
    mistaken for "no violations"."""
    import json
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if "violations" not in data or "unconnected_items" not in data:
        raise ValueError(f"unexpected DRC JSON layout in {path.name}: keys {sorted(data)[:12]}")

    def conv(v, section):
        items = []
        for it in v.get("items", []):
            pos = it.get("pos") or {}
            items.append(f"@({pos.get('x', 0):.4f} mm, {pos.get('y', 0):.4f} mm): {it.get('description', '')}")
        sev = str(v.get("severity", "error")).lower()
        return DrcIssue(v.get("type", "unknown"), sev, v.get("description", ""), items, section)

    issues = [conv(v, "violations") for v in data.get("violations", [])]
    issues += [conv(v, "unconnected") for v in data.get("unconnected_items", [])]
    issues += [conv(v, "footprint") for v in data.get("schematic_parity", [])]
    return DrcResult(str(path), len(data.get("violations", [])), len(data.get("unconnected_items", [])),
                     len(data.get("schematic_parity", [])), issues)
