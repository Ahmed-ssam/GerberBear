import csv
import json
import zipfile

import pytest

from ai_pcb_designer.engineering.circuit.model import Circuit
from ai_pcb_designer.engineering.fab.package import build


def _circuit():
    return Circuit.model_validate({
        "name": "demo",
        "board": {"width_mm": 50, "height_mm": 40},
        "nets": [{"name": "GND"}, {"name": "VCC"}],
        "components": [
            {"ref": "R1", "symbol": "Device:R", "value": "10k", "footprint": "Resistor_SMD:R_0603_1608Metric",
             "lcsc": "C25804", "pin_nets": {"1": "VCC", "2": "GND"}},
            {"ref": "R2", "symbol": "Device:R", "value": "10k", "footprint": "Resistor_SMD:R_0603_1608Metric",
             "lcsc": "C25804", "pin_nets": {"1": "VCC", "2": "GND"}},
            {"ref": "R10", "symbol": "Device:R", "value": "1k", "footprint": "Resistor_SMD:R_0603_1608Metric",
             "pin_nets": {"1": "VCC", "2": "GND"}},
            {"ref": "J1", "symbol": "Connector:Conn_01x02", "value": "PWR",
             "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
             "pin_nets": {"1": "VCC", "2": "GND"}},
            {"ref": "R3", "symbol": "Device:R", "value": "0R", "footprint": "Resistor_SMD:R_0603_1608Metric",
             "dnp": True, "pin_nets": {"1": "VCC", "2": "GND"}},
        ]})


def _fab(tmp_path, layers=2):
    fab = tmp_path / "fab"
    g = fab / "gerbers"
    g.mkdir(parents=True)
    for l in ["F_Cu", "B_Cu", "F_Mask", "B_Mask", "F_Silkscreen", "B_Silkscreen", "F_Paste", "Edge_Cuts"] + \
             [f"In{k}_Cu" for k in range(1, layers - 1)]:
        (g / f"demo-{l}.gbr").write_text("G04*")
    (g / "demo-PTH.drl").write_text("M48")
    (g / "demo-PTH-drl_map.gbr").write_text("map")
    (fab / "demo-pos.csv").write_text(
        "Ref,Val,Package,PosX,PosY,Rot,Side\n"
        '"R1","10k","R_0603_1608Metric",10.000000,-5.000000,90.000000,top\n'
        '"R2","10k","R_0603_1608Metric",12.000000,-5.000000,-90.000000,top\n'
        '"R10","1k","R_0603_1608Metric",14.000000,-5.000000,0.000000,bottom\n'
        '"J1","PWR","PinHeader_1x02_P2.54mm_Vertical",20.000000,-8.000000,0.000000,top\n'
        '"R3","0R","R_0603_1608Metric",16.000000,-5.000000,0.000000,top\n'
        '"H1","","MountingHole_3.2mm_M3",3.000000,-3.000000,0.000000,top\n', encoding="utf-8")
    return fab


FACTS = {"width_mm": 50, "height_mm": 40, "copper_layers": 2, "thickness_mm": 1.6, "min_track_mm": 0.25,
         "min_clearance_mm": 0.2, "min_via_diameter_mm": 0.8, "min_via_drill_mm": 0.4, "min_pth_drill_mm": 0.2,
         "min_npth_drill_mm": None, "min_annular_ring_mm": 0.2, "vias": 3,
         "parts": {"top": {"smd": 2, "tht": 1}, "bottom": {"smd": 1, "tht": 0}},
         "tht_refs": ["J1"], "pth_drill_by_ref": {"J1": 0.2}}


def test_order_package(tmp_path):
    fab = _fab(tmp_path)
    pkg = build("demo", _circuit(), fab, FACTS, {"status": "validated"})
    out = fab / "order"
    with zipfile.ZipFile(out / "demo_gerbers.zip") as z:
        names = z.namelist()
    assert "demo-PTH.drl" in names and "demo-F_Cu.gbr" in names
    assert not any("drl_map" in n for n in names)

    bom = list(csv.reader((out / "demo_BOM_JLCPCB.csv").open(encoding="utf-8")))
    assert bom[0] == ["Comment", "Designator", "Footprint", "LCSC Part #"]
    assert ["10k", "R1,R2", "R_0603_1608Metric", "C25804"] in bom
    assert not any("R3" in r[1] for r in bom)                      # DNP left off

    cpl = list(csv.DictReader((out / "demo_CPL_JLCPCB.csv").open(encoding="utf-8")))
    assert [r["Designator"] for r in cpl] == ["J1", "R1", "R2", "R10"]  # natural order, no H1/DNP
    r2 = next(r for r in cpl if r["Designator"] == "R2")
    assert r2["Rotation"] == "270" and r2["Mid X"] == "12mm" and r2["Layer"] == "Top"
    assert next(r for r in cpl if r["Designator"] == "R10")["Layer"] == "Bottom"

    gen = list(csv.DictReader((out / "demo_BOM.csv").open(encoding="utf-8")))
    assert next(r for r in gen if r["Designator"] == "J1")["Type"] == "THT"

    # never invented: lines without a part number are listed for sourcing
    assert len(pkg.needs_sourcing) == 2 and any("R10" in x for x in pkg.needs_sourcing)
    assert any("J1" in n for n in pkg.capability_notes)               # 0.2 mm hole, named part
    spec = json.loads((out / "order_spec.json").read_text())
    assert spec["assembly"]["sides"] == "both"
    readme = (out / "ORDER_README.txt").read_text()
    assert "do not order" not in readme.lower()


def test_order_package_failed_status_is_loud(tmp_path):
    fab = _fab(tmp_path)
    build("demo", _circuit(), fab, FACTS, {"status": "validation_failed"})
    assert "VALIDATION STATUS: VALIDATION_FAILED" in (fab / "order" / "ORDER_README.txt").read_text()


def test_missing_layer_raises(tmp_path):
    fab = _fab(tmp_path, layers=4)
    (fab / "gerbers" / "demo-In2_Cu.gbr").unlink()
    with pytest.raises(RuntimeError, match="In2_Cu"):
        build("demo", _circuit(), fab, dict(FACTS, copper_layers=4), {"status": "validated"})


def test_lcsc_format_validated():
    with pytest.raises(Exception):
        Circuit.model_validate({"name": "x", "board": {"width_mm": 10, "height_mm": 10}, "nets": [],
                                "components": [{"ref": "R1", "symbol": "Device:R", "value": "1k", "lcsc": "25804"}]})


def test_catalogue_matching_rules():
    from ai_pcb_designer.engineering.fab.sourcing import _cap_ok, _res_ok
    d = "100mW Thick Film Resistors 75V ±1% ±100ppm/℃ 10kΩ 0603 Chip Resistor - Surface Mount ROHS"
    assert _res_ok(d, "10k") and not _res_ok(d, "1k") and not _res_ok(d, "100")
    assert not _res_ok("1.1kΩ 0603", "1k")
    c = "50V 100nF X7R ±10% 0603 Multilayer Ceramic Capacitors MLCC - SMD/SMT ROHS"
    assert _cap_ok(c, "100nF") and _cap_ok(c, "100nF 35V") and not _cap_ok(c, "100nF 63V")
    assert not _cap_ok("16V 10uF X5R 0805", "10uF 25V") and _cap_ok("25V 10uF X5R 0805", "10uF 25V")
    assert not _cap_ok(c, "10nF")


class _FakeSourcing:
    errors = []

    def find(self, symbol, value, footprint):
        if symbol == "Device:R" and value == "1k":
            return {"lcsc": "C21190", "mfr": "0603WAF1001T5E", "description": "1kΩ 0603", "basic": True, "stock": 9}
        return None

    def save(self):
        pass


def test_package_uses_confirmed_catalogue_matches(tmp_path):
    fab = _fab(tmp_path)
    pkg = build("demo", _circuit(), fab, FACTS, {"status": "validated"}, sourcing=_FakeSourcing())
    bom = list(csv.reader((fab / "order" / "demo_BOM_JLCPCB.csv").open(encoding="utf-8")))
    assert ["1k", "R10", "R_0603_1608Metric", "C21190"] in bom
    assert pkg.sourcing["matched"] == 1 and not any("R10" in x for x in pkg.needs_sourcing)
    gen = list(csv.DictReader((fab / "order" / "demo_BOM.csv").open(encoding="utf-8")))
    assert next(r for r in gen if r["Designator"] == "R10")["Part number source"].startswith("JLCPCB catalogue match")
    assert next(r for r in gen if r["Designator"] == "R1,R2")["Part number source"] == "design"
