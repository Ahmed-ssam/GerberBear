"""OpenAI, OpenAI-compatible (local servers) and Google Gemini providers (REST, httpx)."""
from __future__ import annotations

import logging
import re

from .base import LLMProvider, Message, ProviderConfig, ProviderError

log = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """Chat Completions API. Also used for OpenAI-compatible servers via base_url."""
    name = "openai"
    DEFAULT_BASE = "https://api.openai.com/v1"

    def _base(self) -> str:
        return (self.cfg.base_url or self.DEFAULT_BASE).rstrip("/")

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            h["Authorization"] = f"Bearer {self.cfg.api_key}"
        elif self.name == "openai":
            raise ProviderError("config", "no OpenAI API key configured")
        return h

    def _chat(self, system: str, messages: list[Message], json_mode: bool) -> str:
        payload = {"model": self.cfg.model, "temperature": self.cfg.temperature,
                   "messages": [{"role": "system", "content": system}] +
                               [{"role": m.role, "content": m.content} for m in messages]}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = self._post(f"{self._base()}/chat/completions", payload, self._headers())
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError("bad_response", f"unexpected response shape: {str(data)[:200]}") from e

    def list_models(self) -> list[str]:
        data = self._get(f"{self._base()}/models", self._headers())
        return sorted(m["id"] for m in data.get("data", []) if "id" in m)


class OpenAICompatibleProvider(OpenAIProvider):
    """Any server exposing /v1/chat/completions (e.g. a local LLM). API key optional."""
    name = "openai_compatible"

    def _base(self) -> str:
        if not self.cfg.base_url:
            raise ProviderError("config", "no base URL configured for the OpenAI-compatible provider")
        return self.cfg.base_url.rstrip("/")


class GeminiProvider(LLMProvider):
    """Google Generative Language API (generateContent)."""
    name = "gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def _headers(self) -> dict:
        if not self.cfg.api_key:
            raise ProviderError("config", "no Gemini API key configured")
        return {"Content-Type": "application/json", "x-goog-api-key": self.cfg.api_key}

    def _chat(self, system: str, messages: list[Message], json_mode: bool) -> str:
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
                         for m in messages],
            "generationConfig": {"temperature": self.cfg.temperature},
        }
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        base = (self.cfg.base_url or self.BASE).rstrip("/")
        data = self._post(f"{base}/models/{self.cfg.model}:generateContent", payload, self._headers())
        try:
            cand = data["candidates"][0]
            parts = cand.get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError("bad_response", f"unexpected response shape: {str(data)[:200]}") from e
        if not text:
            raise ProviderError("bad_response", f"empty response (finishReason={cand.get('finishReason')})")
        return text

    def _fallback(self, err) -> bool:
        """After repeated 429/503 on the chosen model, switch ONCE per session to a sibling model
        of the same tier (flash-lite -> another flash-lite, flash -> another flash), preferring
        older stable versions (usually less loaded, never pricier). Not saved to settings."""
        if getattr(self, "_switched_from", None):
            return False                                    # one switch per session, no model-hopping
        stable = re.compile(r"^gemini-(\d+(?:\.\d+)?)-flash(-lite)?$")
        cur = stable.match(self.cfg.model)
        if not cur:
            return False                                    # user picked a specific/pro model: respect it
        try:
            names = self.list_models()
        except ProviderError:
            return False
        v, lite = float(cur.group(1)), bool(cur.group(2))
        same = [(float(m.group(1)), n) for n in names
                if (m := stable.match(n)) and bool(m.group(2)) == lite and n != self.cfg.model]
        older = sorted((x for x in same if x[0] < v), reverse=True)
        newer = sorted(x for x in same if x[0] > v)
        pick = (older or newer or [None])[0]
        if not pick:
            return False
        log.warning("gemini: %s unavailable (%s); using %s for the rest of this session",
                    self.cfg.model, err.kind, pick[1])
        self._switched_from = self.cfg.model
        self.cfg.model = pick[1]
        return True

    def list_models(self) -> list[str]:
        base = (self.cfg.base_url or self.BASE).rstrip("/")
        data = self._get(f"{base}/models", self._headers())
        return sorted(m["name"].split("/", 1)[-1] for m in data.get("models", [])
                      if "generateContent" in m.get("supportedGenerationMethods", []))


DEFAULT_MODELS = {"openai": "gpt-4.1-mini", "gemini": "gemini-3.1-flash-lite", "openai_compatible": "local-model"}


def make_provider(cfg: ProviderConfig, transport=None) -> LLMProvider:
    if cfg.provider == "openai":
        return OpenAIProvider(cfg, transport)
    if cfg.provider == "gemini":
        return GeminiProvider(cfg, transport)
    if cfg.provider == "openai_compatible":
        return OpenAICompatibleProvider(cfg, transport)
    if cfg.provider == "scripted":
        from .scripted import ScriptedDevProvider, scripted_allowed
        if not scripted_allowed():
            raise ProviderError("config", "the scripted development provider is disabled")
        return ScriptedDevProvider(cfg)
    raise ProviderError("config", f"unknown provider {cfg.provider!r}")
