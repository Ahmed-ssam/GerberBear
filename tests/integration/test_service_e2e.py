"""Headless workflow: conversation -> proposal -> approve -> real KiCad generation -> modify -> rev 2 -> rollback.
Uses the gated scripted development provider (no API key in CI); everything after the
conversation (compile, KiCad, routing, DRC, exports) is the real pipeline."""
import os
from pathlib import Path

import pytest

from ai_pcb_designer.ai.orchestrator import Stage
from ai_pcb_designer.ai.providers.base import ProviderConfig
from ai_pcb_designer.ai.providers.vendors import make_provider
from ai_pcb_designer.core.service import ProjectSession
from ai_pcb_designer.engineering.pipeline import DesignPipeline
from ai_pcb_designer.kicad.detection import detect
from ai_pcb_designer.kicad.library import LibraryManager
from ai_pcb_designer.storage.projects import ProjectManager

CAPS = detect()
JAR = Path(__file__).resolve().parents[2] / "tools" / "freerouting-1.9.0.jar"
pytestmark = pytest.mark.skipif(not (CAPS.installed and CAPS.python_path), reason="KiCad not installed")


def test_full_workflow(tmp_path, monkeypatch):
    monkeypatch.setenv("AIPCB_DEV_SCRIPTED_AI", "1")
    pm = ProjectManager(tmp_path)
    libs = LibraryManager(CAPS.symbol_dir, CAPS.footprint_dir, CAPS.models3d_dir)
    p = pm.create("Fan controller")
    s = ProjectSession(pm, p.id, make_provider(ProviderConfig("scripted", "simulated")), DesignPipeline(CAPS, JAR), libs)
    r = s.user_message("I want an ESP32 board that controls a fan based on temperature")
    assert s.state.stage == Stage.gathering and any("input_voltage" in i.text for i in r.items)
    r = s.user_message("It runs from 12V and the fan draws 0.4A")
    assert s.state.stage == Stage.proposal and any(i.kind == "proposal" for i in r.items)
    assert s.user_message("looks good, go ahead").action == "generate"
    events = []
    out = s.generate(on_event=events.append)
    assert out["revision"] == 1 and out["status"] == "partially_validated", out["summary"]
    assert (out["dir"] / "renders" / "layers" / "board.json").exists()
    assert any(e["type"] == "step_end" and e["name"] == "exports" for e in events)
    assert s.state.stage == Stage.generated
    r = s.user_message("Add a humidity sensor too")
    assert s.state.stage == Stage.proposal and any("sensors" in c for c in s.state.proposal["changes"])
    out2 = s.generate()
    assert out2["revision"] == 2 and out2["status"] == "partially_validated"
    s.rollback(1)
    assert pm.get(p.id).current_revision == 1 and len(pm.revisions(p.id)) == 2
    z = pm.export_native(p.id, 2, tmp_path / "native.zip")
    import zipfile
    names = zipfile.ZipFile(z).namelist()
    assert any(n.endswith(".kicad_pcb") for n in names) and any(n.endswith("fp-lib-table") for n in names)
    # reopening restores the conversation
    s2 = ProjectSession(pm, p.id, None, None, libs)
    assert s2.state.stage == Stage.generated and len(s2.history()) >= 8
