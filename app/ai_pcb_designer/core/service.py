"""Application service for one open project (UI-independent, used by the GUI and tests).

Persists every conversation turn, runs generation into a new revision directory and
records the real run results. Long operations (`user_message`, `generate`) are meant
to be called from a worker thread.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from ..ai.orchestrator import ChatItem, ConversationState, Orchestrator, Stage, TurnResult
from ..ai.providers.base import LLMProvider
from ..engineering.design.compiler import CompileError, compile_spec
from ..engineering.pipeline import DesignPipeline, StepFailed
from ..kicad.cli.runner import Cancelled
from ..kicad.library import LibraryManager
from ..storage.projects import ProjectManager

log = logging.getLogger(__name__)


class ProjectSession:
    def __init__(self, pm: ProjectManager, pid: str, provider: LLMProvider | None, pipeline: DesignPipeline | None,
                 libs: LibraryManager):
        self.pm, self.pid = pm, pid
        self.project = pm.get(pid)
        self.state = ConversationState.from_json(self.project.state) if self.project.state else ConversationState()
        self.libs = libs
        self.pipeline = pipeline
        self.set_provider(provider)

    def set_provider(self, provider: LLMProvider | None):
        self.provider = provider
        self.orch = Orchestrator(provider, self.libs) if provider else None

    # ------------------------------------------------------------ persistence
    def _save(self):
        self.pm.save_state(self.pid, self.state.to_json())

    def _record(self, items: list[ChatItem]):
        for it in items:
            self.pm.add_message(self.pid, it.role, it.kind, it.text, it.data)

    def history(self) -> list[dict]:
        return self.pm.messages(self.pid)

    # ------------------------------------------------------------- dialogue
    def user_message(self, text: str) -> TurnResult:
        self.pm.add_message(self.pid, "user", "text", text)
        if self.orch is None:
            items = [ChatItem("system", "No AI provider is configured. Open Settings > AI to add a provider and "
                                        "API key, or start from an example design on the home screen.", kind="error")]
            self._record(items)
            return TurnResult(items)
        res = self.orch.handle_user_message(self.state, text)
        self._record(res.items)
        self._save()
        return res

    def approve(self) -> TurnResult:
        if not self.orch:
            return TurnResult([])
        res = self.orch.approve(self.state)
        self.pm.add_message(self.pid, "user", "text", "Approved the proposal.")
        self._save()
        return res

    def reject(self) -> TurnResult:
        res = self.orch.reject(self.state) if self.orch else TurnResult([])
        self.pm.add_message(self.pid, "user", "text", "Rejected the proposal.")
        self._record(res.items)
        self._save()
        return res

    def load_spec(self, spec) -> dict:
        """Start from a known-complete spec (example designs): compile and present a proposal."""
        r = compile_spec(spec, self.libs)
        self.state.spec, self.state.proposal, self.state.stage = spec, r.proposal, Stage.proposal
        r.proposal["changes"] = []
        items = [ChatItem("assistant", "Example design loaded. Review the proposal and approve it to generate the "
                                       "schematic and PCB.", kind="text"),
                 ChatItem("assistant", "Design proposal", kind="proposal", data=r.proposal)]
        self._record(items)
        self._save()
        return {"items": items}

    # ------------------------------------------------------------ generation
    def _incremental(self, circuit):
        """Against the latest generated revision: keep designators of unchanged parts and
        the positions of unchanged blocks. Returns (keep map, summary) or ({}, None)."""
        from ..engineering.design import incremental as inc
        try:
            revs = [r for r in self.pm.revisions(self.pid) if r.status not in ("cancelled", "generation_failed")]
        except Exception:
            return {}, None
        if not revs:
            return {}, None
        last = revs[-1]
        old = inc.load_circuit(last.dir)
        pos = inc.previous_positions(last.dir)
        if old is None:
            return {}, None
        info = inc.stabilize(circuit, old)
        keep = inc.keep_map(circuit, old, pos or {}, info["unchanged_blocks"]) if pos else {}
        info.update(base_revision=last.rev, kept_blocks=sorted(keep))
        return keep, info

    def generate(self, on_event=None) -> dict:
        if self.pipeline is None:
            raise RuntimeError("KiCad integration is unavailable; cannot generate (see Settings > KiCad).")
        if self.state.stage != Stage.proposal or not self.state.proposal:
            raise RuntimeError("Nothing approved to generate.")
        prefer = None
        try:
            from ..engineering.design.incremental import load_circuit
            revs = self.pm.revisions(self.pid)
            old = load_circuit(revs[-1].dir) if revs else None
            prefer = (old.board.width_mm, old.board.height_mm) if old else None
        except Exception:
            prefer = None
        try:
            compiled = compile_spec(self.state.spec, self.libs, prefer_size=prefer)
        except CompileError as e:
            raise RuntimeError(str(e)) from e
        changes = self.state.proposal.get("changes", [])
        keep, incr = self._incremental(compiled.circuit)
        rev, rdir = self.pm.new_revision_dir(self.pid)
        self.state.stage = Stage.generating
        self._save()
        status, summary = "validation_failed", {}
        try:
            rec = self.pipeline.run(compiled.circuit, rdir, on_event=on_event, keep=keep)
            status, summary = rec.validation_status, rec.summary
            if incr:
                summary["incremental"] = incr
        except Cancelled:
            status, summary = "cancelled", {"error": "cancelled by user"}
        except StepFailed as e:
            status, summary = "generation_failed", {"error": str(e)}
        (rdir / "circuit.json").write_text(compiled.circuit.model_dump_json(indent=1))
        (rdir / "proposal.json").write_text(json.dumps(compiled.proposal, indent=1, default=str))
        desc = "Initial design" if rev == 1 else ("; ".join(changes)[:200] or "Regenerated")
        self.pm.add_revision(self.pid, rev, rdir, desc, self.state.spec.model_dump(),
                             {"name": compiled.circuit.name, "components": len(compiled.circuit.components)},
                             changes, status, summary)
        if status in ("cancelled", "generation_failed"):
            self.state.stage = Stage.proposal
            items = [ChatItem("system", f"Generation {'cancelled' if status == 'cancelled' else 'failed'}: "
                                        f"{summary.get('error')}. The proposal is unchanged; you can retry.",
                              kind="error")]
        else:
            self.state.stage = Stage.generated
            self.state.approved_spec = self.state.spec
            if self.orch:
                items = self.orch.results_message(self.state, summary, status)
            else:
                self.state.last_results = {"validation_status": status, **summary}
                items = [ChatItem("assistant", "Generation results", kind="results", data=self.state.last_results)]
        self._record(items)
        self._save()
        return {"revision": rev, "dir": rdir, "status": status, "summary": summary, "items": items}

    # ------------------------------------------------------------- revisions
    def current_revision(self):
        p = self.pm.get(self.pid)
        if p.current_revision is None:
            return None
        return self.pm.revision(self.pid, p.current_revision)

    def rollback(self, rev: int) -> ChatItem:
        r = self.pm.revision(self.pid, rev)
        from ..engineering.design.spec import DesignSpec
        self.pm.set_current(self.pid, rev)
        self.state.spec = DesignSpec.model_validate(r.spec)
        self.state.approved_spec = self.state.spec
        self.state.stage = Stage.generated
        item = ChatItem("system", f"Rolled back to revision {rev} ({r.description}). Its files are unchanged; "
                                  "new changes will create a new revision.", kind="text")
        self._record([item])
        self._save()
        return item
