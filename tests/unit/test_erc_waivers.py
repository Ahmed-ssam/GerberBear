from pathlib import Path
from types import SimpleNamespace as N

from ai_pcb_designer.engineering.validation.erc import apply_waivers

REPORT = Path(__file__).parent / "data" / "erc_t4_kicad10.json"


def _erc():
    return {"ran": True, "report": str(REPORT), "counts": {"error": 1, "warning": 1}}


def test_declared_datasheet_pins_are_waived():
    circ = N(components=[N(ref="U5", erc_waive={"4": "V3 tied to VCC"}), N(ref="U6", erc_waive={"5": "strap"})])
    r = apply_waivers(_erc(), circ)
    assert r["counts"] == {"error": 0, "warning": 0}
    assert {w["reason"] for w in r["waived"]} == {"V3 tied to VCC", "strap"}


def test_undeclared_pins_still_count():
    r = apply_waivers(_erc(), N(components=[N(ref="U5", erc_waive={})]))
    assert r["counts"] == {"error": 1, "warning": 1}
