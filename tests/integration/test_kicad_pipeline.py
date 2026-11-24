"""Integration tests against the real KiCad install. Skipped when KiCad is absent."""
import csv
import os
import json
from pathlib import Path

import pytest

from ai_pcb_designer.engineering.circuit.examples import attiny_led_blinker
from ai_pcb_designer.kicad.detection import detect

ROOT = Path(__file__).resolve().parents[2]
JAR = ROOT / "tools" / "freerouting-1.9.0.jar"
CAPS = detect()
pytestmark = pytest.mark.skipif(not (CAPS.installed and CAPS.python_path), reason="KiCad not installed")


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory):
    from ai_pcb_designer.engineering.pipeline import DesignPipeline
    d = tmp_path_factory.mktemp("proj")
    rec = DesignPipeline(CAPS, JAR).run(attiny_led_blinker(), d)
    return d, rec


def test_detection_reports_real_capabilities():
    assert CAPS.version and CAPS.cli_path and CAPS.symbol_dir
    assert CAPS.api_drc  # needed for DRC on KiCad 7


def test_library_resolution_handles_extends():
    from ai_pcb_designer.kicad.library import LibraryManager
    lm = LibraryManager(CAPS.symbol_dir, CAPS.footprint_dir, CAPS.models3d_dir)
    s = lm.resolve_symbol("MCU_Microchip_ATtiny:ATtiny85-20S")  # extends ATtiny25V-10S
    assert {p.number for p in s.pins} == {str(i) for i in range(1, 9)}
    assert s.pin("8").name == "VCC" and s.pin("4").etype == "power_in"
    assert s.properties["Value"] == "ATtiny85-20S"


def test_all_steps_succeeded(run_dir):
    d, rec = run_dir
    bad = [(s.name, s.error) for s in rec.steps if s.status != "ok"]
    assert not bad, bad


def test_schematic_netlist_matches_ir(run_dir):
    d, rec = run_dir
    nl = next(s for s in rec.steps if s.name == "netlist_verification").detail
    assert nl["ir_to_kicad"] == {"+5V": "+5V", "GND": "GND", "LED_DRV": "/LED_DRV", "LED_A": "/LED_A"}


def test_pcb_connectivity_and_links(run_dir):
    """Every pad net in the saved board matches the IR, and footprints link to schematic symbols."""
    from ai_pcb_designer.kicad.pcb.client import PcbWorker
    d, rec = run_dir
    circuit = attiny_led_blinker()
    st = PcbWorker(CAPS.python_path, CAPS.path_env()).call(
        "stats", {"output": str(d / f"{circuit.name}.kicad_pcb")})
    fps = {f["ref"]: f for f in st["footprints"]}
    nm = next(s for s in rec.steps if s.name == "netlist_verification").detail["ir_to_kicad"]
    sch = (d / f"{circuit.name}.kicad_sch").read_text()
    for c in circuit.components:
        assert fps[c.ref]["path"].lstrip("/") in sch          # symbol UUID exists in schematic
        for pin, net in c.pin_nets.items():
            assert fps[c.ref]["pads"][pin] == nm[net]
    assert st["unrouted_connections"] == 0 and st["tracks"] > 0
    assert st["zones"] == 2


def test_drc_clean_and_status_honest(run_dir):
    d, rec = run_dir
    assert rec.summary["drc"] == {"violations": 0, "unconnected": 0, "footprint_errors": 0,
                                  "errors": 0, "warnings": 0}
    # KiCad 7 has no CLI ERC, so a clean DRC must not be reported as fully validated
    expected = "validated" if CAPS.cli_erc else "partially_validated"
    assert rec.validation_status == expected
    assert (d / "reports").glob("drc#*.rpt")


def test_exports_exist_and_are_real(run_dir):
    d, rec = run_dir
    name = attiny_led_blinker().name
    g = d / "fab" / "gerbers"
    for suffix in ("F_Cu.gtl", "B_Cu.gbl", "Edge_Cuts.gm1", "F_Mask.gts"):
        f = g / f"{name}-{suffix}"
        assert f.exists() and f.read_text().startswith("%TF.GenerationSoftware,KiCad")
    assert "M48" in (g / f"{name}-PTH.drl").read_text()           # plated and non-plated drill split
    rows = list(csv.reader((d / "fab" / f"{name}-bom.csv").open()))
    assert {r[0] for r in rows[1:]} == {"C1", "D1", "J1", "R1", "U1"}
    assert (d / "3d" / f"{name}.step").read_text(errors="ignore").startswith("ISO-10303-21")
    assert (d / "3d" / f"{name}.glb").read_bytes()[:4] == b"glTF"
    svg = (d / "renders" / "pcb_top.svg").read_text()
    assert "<svg" in svg and "path" in svg
    exports = next(s for s in rec.steps if s.name == "exports").detail
    assert exports["failed"] == []
    assert exports["components_without_3d_model"] == ["U1"]   # genuine KiCad library gap


def test_run_record_is_traceable(run_dir):
    d, _ = run_dir
    data = json.loads((d / "run.json").read_text())
    assert data["kicad_version"] == CAPS.version
    assert all("seconds" in s for s in data["steps"])


@pytest.mark.parametrize("level", ["L1", "L2", "L3", "L4"])
def test_ladder_schematic_netlist_matches_ir(level, tmp_path):
    """Hierarchical schematics up to 205 symbols: KiCad's netlist must equal the IR exactly."""
    from ai_pcb_designer.engineering.circuit.ladder import LADDER
    from ai_pcb_designer.kicad.library import LibraryManager
    from ai_pcb_designer.kicad.schematic.netlist import compare, export_netlist
    from ai_pcb_designer.kicad.schematic.writer import SchematicWriter
    c = LADDER[level]()
    lm = LibraryManager(CAPS.symbol_dir, CAPS.footprint_dir, CAPS.models3d_dir)
    r = SchematicWriter(lm).write(c, tmp_path)
    assert r.problems == []
    nl = export_netlist(CAPS.cli_path, r.path, tmp_path / "n.net")
    rep = compare(c.connections(), nl)
    assert rep.ok, rep.missing + rep.extra
    assert set(nl.components) == {x.ref for x in c.components}
    assert len(r.files) == 1 + len({x.sheet for x in c.components if x.sheet})


@pytest.mark.skipif(os.environ.get("PCB_SLOW") != "1", reason="set PCB_SLOW=1 (runs ~3-6 min)")
def test_l4_200_part_board_end_to_end(tmp_path):
    """209 parts, 4 layers: routed, DRC clean, every pad on its IR net, full fab package."""
    from ai_pcb_designer.engineering.circuit.ladder import LADDER
    from ai_pcb_designer.engineering.pipeline import DesignPipeline
    from ai_pcb_designer.kicad.pcb.client import PcbWorker
    c = LADDER["L4"]()
    rec = DesignPipeline(CAPS, JAR).run(c, tmp_path)
    assert rec.summary["routing_complete"] and rec.summary["unrouted"] == 0
    assert rec.summary["drc"]["errors"] == 0 and rec.summary["drc"]["unconnected"] == 0
    st = PcbWorker(CAPS.python_path, CAPS.path_env()).call("stats", {"output": str(tmp_path / f"{c.name}.kicad_pcb")})
    nm = next(s for s in rec.steps if s.name == "netlist_verification").detail["ir_to_kicad"]
    fps = {f["ref"]: f for f in st["footprints"]}
    assert len(fps) == len(c.components) + len(c.board.mounting_holes)
    for comp in c.components:
        assert fps[comp.ref]["path"].count("/") == 2           # /<sheet>/<symbol>: hierarchical link
        for pin, net in comp.pin_nets.items():
            assert fps[comp.ref]["pads"][pin] == nm[net]
    gerbers = {Path(f).name.split("-", 1)[1] for r in next(s for s in rec.steps if s.name == "exports")
               .detail["records"] if r["name"] == "gerbers" for f in r["files"]}
    assert {"In1_Cu.g2", "In2_Cu.g3", "F_Cu.gtl", "B_Cu.gbl"} <= gerbers
