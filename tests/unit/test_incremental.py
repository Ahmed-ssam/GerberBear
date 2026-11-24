import pytest

from ai_pcb_designer.engineering.design import incremental as inc
from ai_pcb_designer.engineering.design.compiler import build_circuit
from ai_pcb_designer.engineering.design.spec import DesignSpec
from ai_pcb_designer.engineering.placement.heuristic import footprint_geom, place


@pytest.fixture(scope="module")
def libs():
    from ai_pcb_designer.kicad.detection import detect
    from ai_pcb_designer.kicad.library import LibraryManager
    caps = detect()
    if not caps.symbol_dir:
        pytest.skip("KiCad libraries not installed")
    return LibraryManager(caps.symbol_dir, caps.footprint_dir, caps.models3d_dir)


BASE = {"controller": "esp32", "input_voltage": 12,
        "loads": {"count": 3, "kind": "dc_fan", "voltage": 12, "current_per_channel_a": 0.3},
        "sensors": [{"kind": "ds18b20"}]}


def _geoms(c, libs):
    out = {}
    for comp in c.components:
        fp = comp.footprint or libs.resolve_symbol(comp.symbol).properties["Footprint"]
        out[comp.ref] = footprint_geom(libs.resolve_footprint(fp).path, fp)
    return out


def test_added_sensor_keeps_refs_and_layout(libs):
    old = build_circuit(DesignSpec.model_validate(BASE), 170, 120, 2, 4, libs)
    new = build_circuit(DesignSpec.model_validate(dict(BASE, sensors=[{"kind": "ds18b20"}, {"kind": "bme280"}])),
                        170, 120, 2, 4, libs)
    def refs_by_key(circ):
        out = {}
        for c in circ.components:
            out.setdefault((c.block, c.description, c.value), set()).add(c.ref)
        return out
    old_refs = refs_by_key(old)
    info = inc.stabilize(new, old)
    # every part that existed keeps its designator
    for k, refs in refs_by_key(new).items():
        if k in old_refs:
            assert refs == old_refs[k], (k, refs, old_refs[k])
    assert len({c.ref for c in new.components}) == len(new.components)          # still unique
    assert "ch01" in info["unchanged_blocks"] and "env" not in info["unchanged_blocks"]

    pos = {p.ref: [p.x, p.y, p.rotation] for p in place(old, _geoms(old, libs))}
    keep = inc.keep_map(new, old, pos, info["unchanged_blocks"])
    assert keep
    from ai_pcb_designer.engineering.placement.heuristic import DEBUG
    placed = {p.ref: (p.x, p.y, p.rotation) for p in place(new, _geoms(new, libs), keep=keep)}
    assert not DEBUG.get("keep_dropped")
    for b, parts in keep.items():
        for r, xyz in parts.items():
            assert placed[r] == tuple(map(float, xyz))
    # the new sensor was placed somewhere on the board
    env = [c.ref for c in new.components if c.block == "env"]
    assert env and all(r in placed for r in env)


def test_board_size_change_drops_layout_keep(libs):
    old = build_circuit(DesignSpec.model_validate(BASE), 130, 90, 2, 4, libs)
    new = build_circuit(DesignSpec.model_validate(BASE), 140, 90, 2, 4, libs)
    info = inc.stabilize(new, old)
    assert inc.keep_map(new, old, {c.ref: [10, 10, 0] for c in old.components}, info["unchanged_blocks"]) == {}
