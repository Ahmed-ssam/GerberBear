"""Storage, secrets fallback, path safety, local server and redaction."""
import json
import urllib.request

import pytest

from ai_pcb_designer.core.localserver import ViewerServer
from ai_pcb_designer.core.logging_setup import redact
from ai_pcb_designer.security import secrets
from ai_pcb_designer.security.paths import UnsafePath, safe_join
from ai_pcb_designer.storage.projects import ProjectManager


def test_projects_messages_revisions(tmp_path):
    pm = ProjectManager(tmp_path)
    p = pm.create("My Board", "desc")
    assert p.dir.exists() and pm.list()[0]["name"] == "My Board"
    pm.add_message(p.id, "user", "text", "hello")
    pm.add_message(p.id, "assistant", "proposal", "Design proposal", {"a": 1})
    assert [m["kind"] for m in pm.messages(p.id)] == ["text", "proposal"] and pm.messages(p.id)[1]["data"] == {"a": 1}
    pm.save_state(p.id, {"stage": "proposal"})
    assert pm.get(p.id).state["stage"] == "proposal"
    rev, d = pm.new_revision_dir(p.id)
    (d / "x.kicad_pcb").write_text("(kicad_pcb)")
    (d / "x.kicad_sch").write_text("(kicad_sch)")
    pm.add_revision(p.id, rev, d, "Initial", {"controller": "esp32"}, {"name": "x"}, [], "partially_validated", {})
    assert pm.get(p.id).current_revision == 1 and pm.list()[0]["status"] == "partially_validated"
    z = pm.export_native(p.id, 1, tmp_path / "out.zip")
    import zipfile
    assert sorted(zipfile.ZipFile(z).namelist()) == ["x/x.kicad_pcb", "x/x.kicad_sch"]
    with pytest.raises(FileNotFoundError):
        pm.export_fab(p.id, 1, tmp_path / "fab.zip")


def test_secrets_memory_fallback_never_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(secrets, "_keyring", lambda: None)
    secrets.set_key("openai", "sk-abcdefghijklmnop")
    assert secrets.get_key("openai") == "sk-abcdefghijklmnop"
    assert secrets.backend_status()[0] is False
    secrets.set_key("openai", None)
    assert secrets.get_key("openai") is None


def test_redaction():
    assert "sk-" not in redact("key=sk-abcdefghijklmnopqrstu used")
    assert "AIza" not in redact("AIzaSyA1234567890abcdefghijklmnop")
    assert "secret" not in redact("Authorization: Bearer secretsecretsecret")


def test_redact_filter_keeps_numeric_formats():
    import logging
    from ai_pcb_designer.core.logging_setup import RedactFilter
    r = logging.LogRecord("x", logging.INFO, "f", 1, "run %s rc=%s %.2fs key=%s", ("kicad-cli", 0, 1.234,
                          "sk-abcdefghijklmnopq"), None)
    assert RedactFilter().filter(r) and r.getMessage() == "run kicad-cli rc=0 1.23s key=[REDACTED]"


def test_safe_join(tmp_path):
    assert safe_join(tmp_path, "a", "b") == (tmp_path / "a" / "b").resolve()
    with pytest.raises(UnsafePath):
        safe_join(tmp_path, "..", "etc", "passwd")


def test_viewer_server_serves_only_roots(tmp_path):
    app = tmp_path / "app"; app.mkdir(); (app / "v.html").write_text("ok")
    rev = tmp_path / "rev"; rev.mkdir(); (rev / "b.json").write_text(json.dumps({"x": 1}))
    (tmp_path / "secret.txt").write_text("nope")
    s = ViewerServer(app)
    s.set_revision(rev)
    assert urllib.request.urlopen(s.app_url("v.html")).read() == b"ok"
    assert json.loads(urllib.request.urlopen(s.rev_url("b.json")).read()) == {"x": 1}
    for bad in (s.app_url("../secret.txt"), s.app_url("%2e%2e/secret.txt"),
                f"http://127.0.0.1:{s.port}/rev/wrongtoken/b.json"):
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(bad)
    s.stop()
