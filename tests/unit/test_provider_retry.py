import httpx

from ai_pcb_designer.ai.providers import base
from ai_pcb_designer.ai.providers.base import Message, ProviderConfig, ProviderError
from ai_pcb_designer.ai.providers.vendors import GeminiProvider

OK = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}
MODELS = {"models": [{"name": f"models/{n}", "supportedGenerationMethods": ["generateContent"]}
                     for n in ("gemini-3.1-flash-lite", "gemini-3-flash", "gemini-3-pro-preview", "gemini-2.5-flash",
                               "gemini-2.5-flash-lite", "gemini-3.8-flash-lite", "gemini-3-flash-image")]}


def _provider(handler):
    cfg = ProviderConfig(provider="gemini", model="gemini-3.1-flash-lite", api_key="k")
    return GeminiProvider(cfg, httpx.MockTransport(handler))


def test_busy_model_falls_back_to_stable_flash(monkeypatch):
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json=MODELS)
        if "3.1-flash-lite" in req.url.path:
            return httpx.Response(503, json={"error": {"message": "high demand"}})
        return httpx.Response(200, json=OK)

    p = _provider(handler)
    assert p.complete_text("s", [Message("user", "x")]) == "hi"
    assert p.cfg.model == "gemini-2.5-flash-lite"          # same tier, older stable version
    assert sum("3.1-flash-lite:generateContent" in c for c in calls) == 5


def test_recovers_after_transient_503(monkeypatch):
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(503, json={}) if n["i"] < 3 else httpx.Response(200, json=OK)

    p = _provider(handler)
    assert p.complete_text("s", [Message("user", "x")]) == "hi"
    assert p.cfg.model == "gemini-3.1-flash-lite"


def test_auth_error_not_retried(monkeypatch):
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(403, json={"error": {"message": "bad key"}})

    p = _provider(handler)
    try:
        p.complete_text("s", [Message("user", "x")])
    except ProviderError as e:
        assert e.kind == "auth"
    assert n["i"] == 1


def test_only_one_switch_and_daily_quota_not_retried(monkeypatch):
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    n = {"gen": 0}

    def handler(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json=MODELS)
        n["gen"] += 1
        return httpx.Response(503, json={})

    p = _provider(handler)
    try:
        p.complete_text("s", [Message("user", "x")])
    except ProviderError as e:
        assert e.kind == "server"
    assert n["gen"] == 10                                   # 5 on the chosen model, 5 on one fallback
    try:
        p.complete_text("s", [Message("user", "x")])
    except ProviderError:
        pass
    assert n["gen"] == 15 and p.cfg.model == "gemini-2.5-flash-lite"   # no further hopping

    q = _provider(lambda req: httpx.Response(429, json={"error": {"message": "Quota exceeded: requests per day"}}))
    try:
        q.complete_text("s", [Message("user", "x")])
    except ProviderError as e:
        assert e.kind == "quota"
