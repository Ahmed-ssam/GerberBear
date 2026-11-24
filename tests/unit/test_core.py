import textwrap

import pytest
from pydantic import ValidationError

from ai_pcb_designer.engineering.circuit.examples import attiny_led_blinker
from ai_pcb_designer.engineering.circuit.model import Circuit, Component, Net, NetKind
from ai_pcb_designer.engineering.validation.drc import parse_report
from ai_pcb_designer.kicad import sexpr
from ai_pcb_designer.kicad.cli.runner import run


def test_sexpr_roundtrip_preserves_quoting():
    src = '(a (b "x y" 1.27) (c yes) (d "q\\"uote"))'
    tree = sexpr.parse(src)
    assert isinstance(tree[1][1], str) and not isinstance(tree[1][1], sexpr.Sym)
    assert isinstance(tree[2][1], sexpr.Sym)
    assert sexpr.parse(sexpr.dumps(tree)) == tree


def test_sexpr_unbalanced_raises():
    with pytest.raises(ValueError):
        sexpr.parse("(a (b)")


def test_circuit_rejects_undefined_net():
    c = attiny_led_blinker().model_dump()
    c["components"][0]["pin_nets"]["1"] = "NOPE"
    with pytest.raises(ValidationError, match="undefined net"):
        Circuit.model_validate(c)


def test_circuit_rejects_duplicate_refs():
    c = attiny_led_blinker().model_dump()
    c["components"][1]["ref"] = c["components"][0]["ref"]
    with pytest.raises(ValidationError, match="duplicate"):
        Circuit.model_validate(c)


def test_power_net_requires_symbol():
    with pytest.raises(ValidationError):
        Net(name="+12V", kind=NetKind.power)


def test_connections_and_warnings():
    c = attiny_led_blinker()
    conns = c.connections()
    assert conns["GND"] == {("J1", "2"), ("U1", "4"), ("C1", "2"), ("D1", "1")}
    assert c.warnings() == []


def test_runner_blocks_unlisted_executables():
    with pytest.raises(PermissionError):
        run(["bash", "-c", "echo hi"])


DRC_SAMPLE = textwrap.dedent("""\
    ** Drc report for /x.kicad_pcb **
    ** Found 2 DRC violations **
    [clearance]: Clearance violation (netclass 'Default' clearance 0.2000 mm; actual 0.1000 mm)
        Rule: netclass 'Default'; Severity: error
        @(10.0000 mm, 5.0000 mm): Track [GND] on F.Cu
        @(10.1000 mm, 5.0000 mm): Pad 1 [+5V] of C1 on F.Cu
    [silk_overlap]: Silkscreen overlap
        Rule: board setup constraints silk; Severity: warning
        @(1.0000 mm, 1.0000 mm): Reference 'C1'

    ** Found 1 unconnected pads **
    [unconnected_items]: Missing connection between items
        Local override; Severity: error
        @(1.0000 mm, 2.0000 mm): Pad 1 of R1
        @(3.0000 mm, 2.0000 mm): Pad 2 of D1

    ** Found 0 Footprint errors **
    ** End of Report **
""")


def test_drc_parser(tmp_path):
    p = tmp_path / "drc.rpt"
    p.write_text(DRC_SAMPLE)
    r = parse_report(p)
    assert (r.violations, r.unconnected, r.footprint_errors) == (2, 1, 0)
    assert r.errors == 2 and r.warnings == 1
    kinds = [(i.kind, i.severity, i.section, len(i.items)) for i in r.issues]
    assert kinds == [("clearance", "error", "violations", 2), ("silk_overlap", "warning", "violations", 1),
                     ("unconnected_items", "error", "unconnected", 2)]
