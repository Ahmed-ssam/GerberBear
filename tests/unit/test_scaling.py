"""Builder, blocks, ladder and placement tests (no KiCad needed except library files)."""
from pathlib import Path

import pytest

from ai_pcb_designer.engineering.circuit.builder import CircuitBuilder
from ai_pcb_designer.engineering.circuit.ladder import LADDER
from ai_pcb_designer.engineering.circuit.model import BoardSpec
from ai_pcb_designer.engineering.placement.heuristic import Box, rot
from ai_pcb_designer.engineering.validation.drc import DrcIssue, DrcResult
from ai_pcb_designer.engineering.validation.repair import plan

LIBS = Path("/usr/share/kicad/footprints")


def test_builder_auto_refdes_and_blocks():
    b = CircuitBuilder("t", "t", BoardSpec(width_mm=50, height_mm=50))
    b.power("GND")
    with b.block("a", sheet="S1"):
        r1 = b.R("1k", "X", "GND")
        r2 = b.R("1k", "X", "Y")
    c = b.build()
    assert (r1, r2) == ("R1", "R2")
    assert {x.block for x in c.components} == {"a"} and {x.sheet for x in c.components} == {"S1"}


@pytest.mark.parametrize("level,min_parts", [("L1", 30), ("L2", 70), ("L3", 140), ("L4", 200)])
def test_ladder_sizes_and_no_dangling_nets(level, min_parts):
    c = LADDER[level]()
    assert len(c.components) + len(c.board.mounting_holes) >= min_parts
    assert c.warnings() == []          # every net has >= 2 connections


def test_rotation_matches_kicad_convention():
    # KiCad: +90 deg is counter-clockwise on a Y-down screen, so "right" turns into "up"
    assert rot(1, 0, 90) == (0, -1)
    assert rot(0, -1, 90) == (-1, 0)
    assert Box(0, 0, 2, 1).rotated(90) == Box(0, -2, 1, 0)


def test_repair_planner_escalates_and_classifies():
    def res(*issues):
        return DrcResult("x", len(issues), 0, 0, list(issues))
    silk = DrcIssue("silk_overlap", "warning", "", ["@(1 mm, 1 mm): Reference 'R4'"])
    starved = DrcIssue("starved_thermal", "error", "", ["@(0.5 mm, 0.5 mm): Zone [GND] on F.Cu",
                                                       "@(2 mm, 2 mm): PTH pad 39 [GND] of U2"])
    hist: dict = {}
    p1 = plan(res(silk, starved), hist)
    assert {o["op"] for o in p1.ops} == {"shrink_reference", "solid_zone_connection"}
    assert ["U2", "39"] in [list(x) for x in p1.ops[-1]["pads"]] or ("U2", "39") in p1.ops[-1]["pads"]
    p2 = plan(res(silk), hist)                        # still overlapping -> hide
    assert p2.ops == [{"op": "hide_reference", "refs": ["R4"]}]
    p3 = plan(res(DrcIssue("clearance", "error", "", [])), hist)
    assert p3.relayout == ["clearance"] and not p3.ops
    p4 = plan(res(DrcIssue("drill_out_of_range", "error", "x", [])), hist)
    assert p4.empty and p4.unfixable
