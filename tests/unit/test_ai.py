"""AI layer tests. Providers are exercised against mocked HTTP responses in the documented
formats (no network); the orchestrator runs with a queue-based fake model (test-only)."""
import json

import httpx
import pytest

from ai_pcb_designer.ai.orchestrator import ConversationState, Orchestrator, Stage, _with_context
from ai_pcb_designer.ai.providers.base import LLMProvider, Message, ProviderConfig, ProviderError, parse_json
from ai_pcb_designer.ai.providers.vendors import GeminiProvider, OpenAIProvider, make_provider
from ai_pcb_designer.kicad.library import LibraryManager

LIBS = LibraryManager("/usr/share/kicad/symbols", "/usr/share/kicad/footprints", "/usr/share/kicad/3dmodels")


def test_openai_request_and_response_shape():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers.get("authorization"), json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": '{"a": 1}'}}]})
    p = OpenAIProvider(ProviderConfig("openai", "m1", api_key="sk-test"), transport=httpx.MockTransport(handler))
    assert p.complete_json("sys", [Message("user", "hi")]) == {"a": 1}
    assert seen["url"] == "https://api.openai.com/v1/chat/completions" and seen["auth"] == "Bearer sk-test"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}


def test_gemini_request_and_response_shape():
    seen = {}

    def handler(req):
        seen["url"], seen["key"], seen["body"] = str(req.url), req.headers.get("x-goog-api-key"), json.loads(req.content)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "hello"}], "role": "model"},
                                                         "finishReason": "STOP"}]})
    p = GeminiProvider(ProviderConfig("gemini", "gemini-x", api_key="g-key"), transport=httpx.MockTransport(handler))
    assert p.complete_text("sys", [Message("user", "a"), Message("assistant", "b"), Message("user", "c")]) == "hello"
    assert seen["url"].endswith("/models/gemini-x:generateContent") and seen["key"] == "g-key"
    assert [c["role"] for c in seen["body"]["contents"]] == ["user", "model", "user"]
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "sys"


def test_error_mapping_and_rate_limit_retry(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(429, json={"error": {"message": "slow down"}}) if calls["n"] < 2 else \
            httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    p = OpenAIProvider(ProviderConfig("openai", "m", api_key="k"), transport=httpx.MockTransport(handler))
    assert p.complete_text("s", [Message("user", "x")]) == "ok" and calls["n"] == 2
    bad = OpenAIProvider(ProviderConfig("openai", "m", api_key="k"),
                         transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"error": {"message": "no"}})))
    with pytest.raises(ProviderError) as e:
        bad.complete_text("s", [Message("user", "x")])
    assert e.value.kind == "auth"
    with pytest.raises(ProviderError) as e:
        OpenAIProvider(ProviderConfig("openai", "m")).complete_text("s", [Message("user", "x")])
    assert e.value.kind == "config"


def test_json_parsing_and_invalid_json_retry():
    assert parse_json('```json\n{"x": 2}\n```') == {"x": 2}
    replies = iter(["not json", '{"ok": true}'])
    p = OpenAIProvider(ProviderConfig("openai", "m", api_key="k"), transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": next(replies)}}]})))
    assert p.complete_json("s", [Message("user", "x")]) == {"ok": True}


def test_scripted_provider_is_gated(monkeypatch):
    monkeypatch.delenv("AIPCB_DEV_SCRIPTED_AI", raising=False)
    with pytest.raises(ProviderError):
        make_provider(ProviderConfig("scripted", "simulated"))


def test_context_folding_keeps_roles_alternating():
    h = [Message("assistant", "a"), Message("user", "u1"), Message("assistant", "a1"), Message("user", "u2")]
    out = _with_context(h, "CTX")
    assert [m.role for m in out] == ["user", "assistant", "user"] and out[-1].content.endswith("CTX")


class QueueModel(LLMProvider):
    """Test double: returns queued responses; records system prompts."""
    name = "queue"

    def __init__(self, responses):
        super().__init__(ProviderConfig("queue", "q"))
        self.responses, self.systems = list(responses), []

    def _chat(self, system, messages, json_mode):
        self.systems.append(system[:30])
        r = self.responses.pop(0)
        return json.dumps(r) if isinstance(r, dict) else r

    def list_models(self):
        return []


def test_orchestrator_gathering_proposal_approval_and_modification():
    ex = lambda intent, upd: {"intent": intent, "spec_updates": upd, "user_question": None}  # noqa: E731
    model = QueueModel([
        ex("describe", {"controller": "esp32", "loads": {"count": 2, "kind": "dc_fan"}}), "Which voltage?",
        ex("answer", {"input_voltage": 12, "loads": {"voltage": 12, "current_per_channel_a": 0.4}}), "Here is a plan.",
        ex("approve", {}),
        ex("modify", {"loads": {"count": 3}}), "Updated plan.",
    ])
    orch, st = Orchestrator(model, LIBS), ConversationState()
    r1 = orch.handle_user_message(st, "ESP32 board with two fans")
    assert st.stage == Stage.gathering and r1.items[0].text == "Which voltage?" and r1.compiled is None
    r2 = orch.handle_user_message(st, "12 V, 0.4 A each")
    assert st.stage == Stage.proposal and r2.compiled is not None
    assert any(i.kind == "proposal" for i in r2.items) and st.proposal["totals"]["components"] > 30
    r3 = orch.handle_user_message(st, "looks good, go ahead")
    assert r3.action == "generate"
    st.stage, st.approved_spec = Stage.generated, st.spec       # what the UI does after a successful run
    r4 = orch.handle_user_message(st, "make it three fans")
    assert st.stage == Stage.proposal and "loads.count: 2 -> 3" in st.proposal["changes"]
    assert model.systems[0].startswith("You are the requirements") and model.systems[1].startswith("You are the engineering")


def test_orchestrator_never_defaults_critical_values_and_reports_provider_errors():
    model = QueueModel([{"intent": "describe", "spec_updates": {"controller": "esp32", "loads": {"count": 4}},
                         "user_question": None}, "Need voltage and current."])
    st = ConversationState()
    Orchestrator(model, LIBS).handle_user_message(st, "4 outputs please")
    assert st.stage == Stage.gathering and st.spec.input_voltage is None

    class Down(QueueModel):
        def _chat(self, *a):
            raise ProviderError("network", "offline")
    r = Orchestrator(Down([]), LIBS).handle_user_message(ConversationState(), "hello")
    assert r.items[0].kind == "error" and "reach the AI provider" in r.items[0].text


def _proposal_state(extra_upd=None):
    ex = lambda intent, upd: {"intent": intent, "spec_updates": upd, "user_question": None}  # noqa: E731
    first = ex("describe", {"controller": "esp32", "input_voltage": 12,
                            "loads": {"count": 2, "kind": "dc_fan", "voltage": 12, "current_per_channel_a": 0.4}})
    return ex, first


def test_plain_approval_with_side_remarks_still_approves():
    ex, first = _proposal_state()
    model = QueueModel([first, "Plan.",
                        ex("other", {"unsupported_requests": ["note: relays are rated 10 A, which is compatible"]})])
    orch, st = Orchestrator(model, LIBS), ConversationState()
    orch.handle_user_message(st, "ESP32, two 12 V fans at 0.4 A")
    assert st.stage == Stage.proposal
    assert orch.handle_user_message(st, "Looks good, go ahead.").action == "generate"


def test_resolved_unsupported_items_are_removed():
    ex, first = _proposal_state()
    first["spec_updates"]["unsupported_requests"] = ["2 A MOSFET outputs"]
    model = QueueModel([first, "Can't do 2 A.",
                        {"intent": "answer", "spec_updates": {}, "user_question": None,
                         "resolved_unsupported": ["2 A MOSFET outputs"]}, "Plan."])
    orch, st = Orchestrator(model, LIBS), ConversationState()
    orch.handle_user_message(st, "ESP32, two 12 V fans, 2 A each")
    assert st.stage == Stage.gathering
    orch.handle_user_message(st, "use 0.4 A then")
    assert st.spec.unsupported_requests == [] and st.stage == Stage.proposal
