"""Conversation state machine: gathering -> proposal -> generating -> generated (-> modify).

Each user turn = extraction call (JSON, validated) -> deterministic analysis/compile ->
reply call. The AI never decides readiness, never writes files and never reports
validation results it did not receive from the engine.
"""
from __future__ import annotations

import re

import json
import logging
from dataclasses import dataclass, field
from enum import Enum

from pydantic import ValidationError

from ..engineering.design.compiler import CompileError, CompileResult, compile_spec
from ..engineering.design.spec import DesignSpec, analyze
from .prompts import EXTRACT_SYSTEM, REPLY_SYSTEM
from .providers.base import LLMProvider, Message, ProviderError

log = logging.getLogger(__name__)
INTENTS = {"describe", "answer", "approve", "reject", "modify", "question", "other"}


class Stage(str, Enum):
    gathering = "gathering"
    proposal = "proposal"
    generating = "generating"
    generated = "generated"


@dataclass
class ChatItem:
    role: str                       # user | assistant | system
    text: str
    kind: str = "text"              # text | proposal | results | error | progress
    data: dict | None = None


@dataclass
class ConversationState:
    spec: DesignSpec = field(default_factory=DesignSpec)
    stage: Stage = Stage.gathering
    history: list[Message] = field(default_factory=list)
    proposal: dict | None = None
    approved_spec: DesignSpec | None = None       # spec of the last generated revision
    last_results: dict | None = None

    def to_json(self) -> dict:
        return {"spec": self.spec.model_dump(), "stage": self.stage.value,
                "history": [m.__dict__ for m in self.history[-60:]], "proposal": self.proposal,
                "approved_spec": self.approved_spec.model_dump() if self.approved_spec else None,
                "last_results": self.last_results}

    @classmethod
    def from_json(cls, d: dict) -> "ConversationState":
        st = cls(spec=DesignSpec.model_validate(d.get("spec") or {}), stage=Stage(d.get("stage", "gathering")),
                 history=[Message(**m) for m in d.get("history", [])], proposal=d.get("proposal"),
                 approved_spec=DesignSpec.model_validate(d["approved_spec"]) if d.get("approved_spec") else None,
                 last_results=d.get("last_results"))
        if st.stage == Stage.generating:          # interrupted run: back to the proposal
            st.stage = Stage.proposal if st.proposal else Stage.gathering
        return st


@dataclass
class TurnResult:
    items: list[ChatItem]
    action: str | None = None              # "generate" when the UI should start the pipeline
    compiled: CompileResult | None = None


def spec_diff(old: DesignSpec | None, new: DesignSpec) -> list[str]:
    if old is None:
        return []
    a, b = old.model_dump(), new.model_dump()
    out = []

    def walk(x, y, path=""):
        for k in sorted(set(x) | set(y)):
            p = f"{path}{k}"
            if isinstance(x.get(k), dict) and isinstance(y.get(k), dict):
                walk(x[k], y[k], p + ".")
            elif x.get(k) != y.get(k) and k not in ("notes",):
                fmt = (lambda v: ", ".join(s["kind"] for s in v) or "none") if k == "sensors" else repr
                out.append(f"{p}: {fmt(x.get(k))} -> {fmt(y.get(k))}")
    walk(a, b)
    return out


_APPROVAL = re.compile(r"\b(looks? good|go ahead|approved?|generate( it)?|build it|proceed|ship it|"
                       r"let'?s do it|sounds good|yes,? (please|do it|go)|ok(ay)?,? (go|do it|build))\b", re.I)
_CHANGE_WORDS = re.compile(r"\b(but|change|instead|except|add|remove|make it|replace)\b", re.I)


class Orchestrator:
    def __init__(self, provider: LLMProvider, libs):
        self.provider = provider
        self.libs = libs

    # ------------------------------------------------------------ user turn
    def handle_user_message(self, state: ConversationState, text: str) -> TurnResult:
        state.history.append(Message("user", text))
        try:
            ext = self._extract(state)
        except ProviderError as e:
            state.history.pop()
            return TurnResult([ChatItem("system", e.user_message(), kind="error")])
        intent = ext.get("intent", "other")
        items: list[ChatItem] = []
        updates = ext.get("spec_updates") or {}
        # A plain "looks good, go ahead" on a proposal is an approval even when the model
        # attached only side remarks (notes / unsupported items) to it.
        if (state.stage == Stage.proposal and intent != "approve" and _APPROVAL.search(text)
                and not _CHANGE_WORDS.search(text) and set(updates) <= {"notes", "unsupported_requests"}):
            intent = "approve"

        if intent == "approve" and state.stage == Stage.proposal:
            state.history.append(Message("assistant", "Approved. Generating the design now."))
            return TurnResult([ChatItem("assistant", "Approved. Generating the schematic and PCB now; progress is "
                                                     "shown below and in the status bar.")], action="generate")

        resolved = [str(x).lower() for x in (ext.get("resolved_unsupported") or []) if str(x).strip()]
        if resolved and state.spec.unsupported_requests:
            keep = [u for u in state.spec.unsupported_requests
                    if not any(r in u.lower() or u.lower() in r for r in resolved)]
            state.spec = state.spec.model_copy(update={"unsupported_requests": keep})
        try:
            new_spec = state.spec.merged(updates)
        except ValidationError as e:
            log.warning("spec update rejected: %s", e)
            new_spec = state.spec
            items.append(ChatItem("system", "Part of that answer could not be applied to the requirements "
                                            f"(invalid value): {e.errors()[0].get('msg')}", kind="error"))
        changed = spec_diff(state.spec, new_spec)
        state.spec = new_spec
        if intent == "reject":
            state.stage, state.proposal = Stage.gathering, None

        an = analyze(state.spec)
        compiled = None
        if an.ready and (changed or state.stage in (Stage.gathering,) or intent in ("modify", "describe", "answer")):
            if state.stage == Stage.generated and not changed:
                pass                                    # question about an existing board: no new proposal
            else:
                try:
                    compiled = compile_spec(state.spec, self.libs)
                    state.proposal = compiled.proposal
                    state.proposal["changes"] = spec_diff(state.approved_spec, state.spec)
                    state.stage = Stage.proposal
                except CompileError as e:
                    an.conflicts.append(str(e))
                    an.ready = False
                    state.stage = Stage.gathering if state.stage != Stage.generated else state.stage
        elif not an.ready and state.stage == Stage.proposal:
            state.stage, state.proposal = Stage.gathering, None

        try:
            reply = self._reply(state, an.to_dict(), ext.get("user_question"))
        except ProviderError as e:
            reply = None
            items.append(ChatItem("system", e.user_message(), kind="error"))
        if reply:
            state.history.append(Message("assistant", reply))
            items.append(ChatItem("assistant", reply))
        if compiled is not None:
            items.append(ChatItem("assistant", "Design proposal", kind="proposal", data=state.proposal))
        return TurnResult(items, compiled=compiled)

    def approve(self, state: ConversationState) -> TurnResult:
        if state.stage != Stage.proposal or not state.proposal:
            return TurnResult([ChatItem("system", "There is no proposal to approve yet.", kind="error")])
        state.history.append(Message("user", "I approve the proposal."))
        return TurnResult([], action="generate")

    def reject(self, state: ConversationState) -> TurnResult:
        state.stage, state.proposal = Stage.gathering, None
        state.history.append(Message("user", "I reject this proposal; let's change it."))
        return TurnResult([ChatItem("assistant", "Proposal discarded. Tell me what to change: board size, "
                                                 "outputs, current, sensors, controller or layers.")])

    # ------------------------------------------------------- after a run
    def results_message(self, state: ConversationState, summary: dict, status: str) -> list[ChatItem]:
        """Deterministic results card + optional AI explanation of the same facts."""
        state.last_results = {"validation_status": status, **{k: summary.get(k) for k in (
            "routing_complete", "unrouted", "drc", "unrouted_connections", "recommendations", "validation_scope", "functional",
            "tracks", "vias")}}
        items = [ChatItem("assistant", "Generation results", kind="results", data=state.last_results)]
        try:
            text = self._reply(state, {"ready": True}, None)
            state.history.append(Message("assistant", text))
            items.insert(0, ChatItem("assistant", text))
        except ProviderError as e:
            items.append(ChatItem("system", e.user_message(), kind="error"))
        return items

    # --------------------------------------------------------------- calls
    def _extract(self, state: ConversationState) -> dict:
        ctx = (f"CURRENT_STAGE: {state.stage.value}\nCURRENT_SPEC: {json.dumps(state.spec.model_dump())}\n"
               "Return the JSON object for the latest user message.")
        msgs = _with_context(state.history, ctx)

        def validate(d):
            if d.get("intent") not in INTENTS:
                raise ValueError(f"intent must be one of {sorted(INTENTS)}")
            if not isinstance(d.get("spec_updates", {}), dict):
                raise ValueError("spec_updates must be an object")
            state.spec.merged(d.get("spec_updates") or {})     # raises if invalid
        return self.provider.complete_json(EXTRACT_SYSTEM, msgs, validate=validate)

    def _reply(self, state: ConversationState, analysis: dict, question) -> str:
        block = {"stage": state.stage.value, "spec": state.spec.model_dump(), "analysis": analysis,
                 "user_question": question}
        if state.stage == Stage.proposal and state.proposal:
            block["proposal"] = {k: state.proposal.get(k) for k in (
                "overview", "architecture", "totals", "board", "assumptions", "risks", "changes")}
        if state.stage == Stage.generated and state.last_results:
            block["results"] = state.last_results
        msgs = _with_context(state.history, "STATE:\n" + json.dumps(block, default=str) +
                             "\nWrite your reply to the user now.")
        return self.provider.complete_text(REPLY_SYSTEM, msgs).strip()


def _with_context(history: list[Message], ctx: str, window: int = 16) -> list[Message]:
    """Recent history starting on a user turn, with `ctx` folded into the final user turn so
    roles strictly alternate (Gemini rejects consecutive same-role turns)."""
    msgs = list(history[-window:])
    while msgs and msgs[0].role != "user":
        msgs.pop(0)
    merged: list[Message] = []
    for m in msgs:                                   # collapse accidental same-role runs
        if merged and merged[-1].role == m.role:
            merged[-1] = Message(m.role, merged[-1].content + "\n\n" + m.content)
        else:
            merged.append(Message(m.role, m.content))
    if merged and merged[-1].role == "user":
        merged[-1] = Message("user", merged[-1].content + "\n\n---\n" + ctx)
    else:
        merged.append(Message("user", ctx))
    return merged
