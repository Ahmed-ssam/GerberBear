"""Local project management: create/open/list projects, persist conversation, record
design revisions (each revision is a complete KiCad project directory), rollback and export."""
from __future__ import annotations

import json
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ..security.paths import safe_join
from .db import Database


@dataclass
class Project:
    id: str
    name: str
    description: str
    dir: Path
    created: float
    modified: float
    state: dict
    current_revision: int | None


@dataclass
class Revision:
    rev: int
    ts: float
    description: str
    dir: Path
    status: str | None
    summary: dict
    changes: list
    spec: dict
    circuit_name: str


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()[:40] or "project"


class ProjectManager:
    def __init__(self, root: Path):
        self.root = root
        self.projects_dir = root / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(root / "projects.sqlite3")

    # ------------------------------------------------------------- projects
    def create(self, name: str, description: str = "") -> Project:
        name = name.strip() or "Untitled PCB"
        pid = uuid.uuid4().hex[:12]
        d = safe_join(self.projects_dir, f"{_slug(name)}-{pid}")
        (d / "revisions").mkdir(parents=True)
        now = self.db.now()
        self.db.execute("INSERT INTO projects (id,name,description,dir,created,modified) VALUES (?,?,?,?,?,?)",
                        (pid, name, description, str(d), now, now))
        (d / "project.json").write_text(json.dumps({"id": pid, "name": name, "created": now}))
        return self.get(pid)

    def get(self, pid: str) -> Project:
        rows = self.db.query("SELECT * FROM projects WHERE id=?", (pid,))
        if not rows:
            raise KeyError(f"project {pid} not found")
        r = rows[0]
        d = Path(r["dir"])
        if not d.exists():
            raise FileNotFoundError(f"project folder missing: {d}")
        return Project(r["id"], r["name"], r["description"], d, r["created"], r["modified"],
                       json.loads(r["state_json"] or "{}"), r["current_revision"])

    def list(self, limit: int = 50) -> list[dict]:
        out = []
        for r in self.db.query("SELECT p.*, (SELECT status FROM revisions v WHERE v.project_id=p.id AND "
                               "v.rev=p.current_revision) AS status, (SELECT COUNT(*) FROM revisions v WHERE "
                               "v.project_id=p.id) AS nrev FROM projects p ORDER BY modified DESC LIMIT ?", (limit,)):
            out.append({"id": r["id"], "name": r["name"], "description": r["description"], "modified": r["modified"],
                        "status": r["status"], "revisions": r["nrev"], "missing": not Path(r["dir"]).exists(),
                        "stage": json.loads(r["state_json"] or "{}").get("stage")})
        return out

    def rename(self, pid: str, name: str):
        self.db.execute("UPDATE projects SET name=?, modified=? WHERE id=?", (name, self.db.now(), pid))

    def delete(self, pid: str, remove_files: bool = False):
        p = self.get(pid)
        self.db.execute("DELETE FROM projects WHERE id=?", (pid,))
        if remove_files:
            shutil.rmtree(safe_join(self.projects_dir, p.dir.name), ignore_errors=True)

    def save_state(self, pid: str, state: dict):
        self.db.execute("UPDATE projects SET state_json=?, modified=? WHERE id=?",
                        (self.db.j(state), self.db.now(), pid))

    # ------------------------------------------------------------- messages
    def add_message(self, pid: str, role: str, kind: str, text: str, data: dict | None = None):
        self.db.execute("INSERT INTO messages (project_id,ts,role,kind,text,data_json) VALUES (?,?,?,?,?,?)",
                        (pid, self.db.now(), role, kind, text, self.db.j(data) if data is not None else None))

    def messages(self, pid: str) -> list[dict]:
        return [{"role": r["role"], "kind": r["kind"], "text": r["text"], "ts": r["ts"],
                 "data": json.loads(r["data_json"]) if r["data_json"] else None}
                for r in self.db.query("SELECT * FROM messages WHERE project_id=? ORDER BY id", (pid,))]

    # ------------------------------------------------------------ revisions
    def new_revision_dir(self, pid: str) -> tuple[int, Path]:
        p = self.get(pid)
        n = (self.db.query("SELECT MAX(rev) AS m FROM revisions WHERE project_id=?", (pid,))[0]["m"] or 0) + 1
        d = safe_join(p.dir, "revisions", f"r{n:03d}")
        d.mkdir(parents=True, exist_ok=False)
        return n, d

    def add_revision(self, pid: str, rev: int, rdir: Path, description: str, spec: dict, circuit: dict,
                     changes: list, status: str, summary: dict):
        self.db.execute("INSERT INTO revisions (project_id,rev,ts,description,spec_json,circuit_json,changes_json,dir,"
                        "status,summary_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (pid, rev, self.db.now(), description, self.db.j(spec), self.db.j(circuit),
                         self.db.j(changes), str(rdir), status, self.db.j(summary)))
        self.set_current(pid, rev)

    def set_current(self, pid: str, rev: int):
        self.db.execute("UPDATE projects SET current_revision=?, modified=? WHERE id=?", (rev, self.db.now(), pid))

    def revisions(self, pid: str) -> list[Revision]:
        out = []
        for r in self.db.query("SELECT * FROM revisions WHERE project_id=? ORDER BY rev", (pid,)):
            circ = json.loads(r["circuit_json"] or "{}")
            out.append(Revision(r["rev"], r["ts"], r["description"] or "", Path(r["dir"]), r["status"],
                                json.loads(r["summary_json"] or "{}"), json.loads(r["changes_json"] or "[]"),
                                json.loads(r["spec_json"] or "{}"), circ.get("name", "")))
        return out

    def revision(self, pid: str, rev: int) -> Revision:
        for r in self.revisions(pid):
            if r.rev == rev:
                return r
        raise KeyError(f"revision {rev} not found")

    # --------------------------------------------------------------- export
    def export_native(self, pid: str, rev: int, dest: Path) -> Path:
        """Zip the native KiCad project (pro, sch + sheets, pcb, lib tables) of a revision."""
        r = self.revision(pid, rev)
        name = r.circuit_name
        files = [f for f in r.dir.iterdir() if f.is_file() and (
            f.suffix in (".kicad_pro", ".kicad_sch", ".kicad_pcb") or f.name in ("fp-lib-table", "sym-lib-table"))]
        if not any(f.suffix == ".kicad_pcb" for f in files):
            raise FileNotFoundError("revision has no PCB file")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                z.write(f, f"{name}/{f.name}")
        return dest

    def export_order(self, pid: str, rev: int, dest_dir: Path) -> Path | None:
        """Copy the revision's order package into a new folder under dest_dir. None if the
        revision predates order packages."""
        r = self.revision(pid, rev)
        src = r.dir / "fab" / "order"
        if not src.is_dir() or not any(src.iterdir()):
            return None
        out = Path(dest_dir) / f"{r.circuit_name or 'design'}_r{rev}_order"
        k = 2
        while out.exists():
            out = Path(dest_dir) / f"{r.circuit_name or 'design'}_r{rev}_order_{k}"
            k += 1
        shutil.copytree(src, out)
        return out

    def export_fab(self, pid: str, rev: int, dest: Path) -> Path:
        r = self.revision(pid, rev)
        fab = r.dir / "fab"
        if not fab.exists():
            raise FileNotFoundError("revision has no fabrication outputs")
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(fab.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(fab))
        return dest
