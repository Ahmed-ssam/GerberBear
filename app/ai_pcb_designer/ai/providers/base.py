"""LLM provider abstraction.

Providers only turn messages into text or JSON. They never see the file system and
cannot run anything; the orchestrator validates every response before use.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """kind: config | auth | rate_limit | quota | network | bad_response | server"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind

    def user_message(self) -> str:
        return {
            "config": "The AI provider is not configured. Open Settings > AI to choose a provider, model and API key.",
            "auth": "The AI provider rejected the API key. Check it in Settings > AI.",
            "rate_limit": "The AI provider is rate-limiting requests. Wait a moment and try again.",
            "network": "Could not reach the AI provider. Check your internet connection or proxy.",
            "bad_response": "The AI returned a response that could not be used. Try rephrasing, or try again.",
            "quota": "The AI key has used up its quota for this model (free tiers have a daily limit). "
                     "Wait until the quota resets, choose another model in Settings > AI, or enable billing "
                     "for the key.",
            "server": "The AI provider is overloaded or reported a server error, and it did not recover "
                      "after several retries. Try again in a minute, or pick another model in Settings > AI.",
        }.get(self.kind, str(self)) + f"\n\nDetails: {self}"


@dataclass
class Message:
    role: str          # "user" | "assistant"
    content: str


@dataclass
class ProviderConfig:
    provider: str                  # openai | gemini | openai_compatible | scripted
    model: str
    api_key: str | None = None
    base_url: str | None = None
    temperature: float = 0.2
    timeout_s: float = 120.0


class LLMProvider(ABC):
    name = "base"
    simulated = False

    def __init__(self, cfg: ProviderConfig, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        self._client = httpx.Client(timeout=httpx.Timeout(cfg.timeout_s, connect=15.0), transport=transport)

    # -- to implement ------------------------------------------------------
    @abstractmethod
    def _chat(self, system: str, messages: list[Message], json_mode: bool) -> str: ...

    @abstractmethod
    def list_models(self) -> list[str]: ...

    # -- shared --------------------------------------------------------------
    def complete_text(self, system: str, messages: list[Message]) -> str:
        return self._with_retries(lambda: self._chat(system, messages, False))

    def complete_json(self, system: str, messages: list[Message], validate=None) -> dict:
        """JSON-mode completion; on unparsable/invalid output, retry once with the error."""
        msgs = list(messages)
        last_err = None
        for attempt in range(2):
            raw = self._with_retries(lambda: self._chat(system, msgs, True))
            try:
                data = parse_json(raw)
                if validate:
                    validate(data)
                return data
            except Exception as e:           # report back to the model once
                last_err = e
                msgs = msgs + [Message("assistant", raw[:4000]),
                               Message("user", f"That was not valid for the required JSON format: {e}. "
                                               "Reply again with only the corrected JSON object.")]
        raise ProviderError("bad_response", f"invalid JSON after retry: {last_err}")

    # Transient failures: HTTP 429/5xx and timeouts. Public endpoints (notably Gemini's
    # "model is experiencing high demand" 503) can stay busy for tens of seconds, so back
    # off for ~30 s in total, then ask the vendor for a fallback model and try once more.
    RETRY_KINDS = ("rate_limit", "server", "network")
    RETRY_DELAYS = (2.0, 4.0, 8.0, 16.0)

    def _with_retries(self, fn, attempts: int | None = None):
        delays = self.RETRY_DELAYS if attempts is None else self.RETRY_DELAYS[:max(0, attempts - 1)]
        switched = False
        while True:
            for i in range(len(delays) + 1):
                try:
                    return fn()
                except ProviderError as e:
                    if e.kind not in self.RETRY_KINDS:
                        raise
                    if e.kind == "network" and "timeout" not in str(e) and i >= 2:
                        raise           # offline / DNS / proxy: two quick retries are enough
                    if i < len(delays):
                        delay = min(30.0, max(delays[i], getattr(e, "retry_after", 0) or 0)) + random.uniform(0, 1)
                        log.warning("provider %s: %s, retrying in %.0fs", self.name, e.kind, delay)
                        time.sleep(delay)
                        continue
                    if not switched and self._fallback(e):
                        switched = True
                        break           # one more full round on the fallback model
                    raise

    def _fallback(self, err: "ProviderError") -> bool:
        """Switch to another model after repeated transient failures; True if switched."""
        return False

    def _post(self, url: str, payload: dict, headers: dict) -> dict:
        try:
            r = self._client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as e:
            raise ProviderError("network", f"timeout: {e}") from e
        except httpx.HTTPError as e:
            raise ProviderError("network", str(e)) from e
        return self._check(r)

    def _get(self, url: str, headers: dict, params: dict | None = None) -> dict:
        try:
            r = self._client.get(url, headers=headers, params=params)
        except httpx.HTTPError as e:
            raise ProviderError("network", str(e)) from e
        return self._check(r)

    @staticmethod
    def _check(r: httpx.Response) -> dict:
        if r.status_code in (401, 403):
            raise ProviderError("auth", f"HTTP {r.status_code}: {_err_text(r)}")
        if r.status_code == 429 and re.search(r"quota|per day|exhausted|billing", _err_text(r), re.I) \
                and not re.search(r"per minute|retry in", _err_text(r), re.I):
            raise ProviderError("quota", f"HTTP 429: {_err_text(r)}")     # daily quota: retrying won't help
        if r.status_code == 429 or r.status_code >= 500:
            e = ProviderError("rate_limit" if r.status_code == 429 else "server",
                              f"HTTP {r.status_code}: {_err_text(r)}")
            try:
                e.retry_after = float(r.headers.get("retry-after", 0) or 0)
            except ValueError:
                pass
            raise e
        if r.status_code >= 400:
            raise ProviderError("bad_response", f"HTTP {r.status_code}: {_err_text(r)}")
        try:
            return r.json()
        except ValueError as e:
            raise ProviderError("bad_response", "response was not JSON") from e


def _err_text(r: httpx.Response) -> str:
    try:
        j = r.json()
        return str(j.get("error", {}).get("message") or j)[:300]
    except ValueError:
        return r.text[:300]


def parse_json(raw: str) -> dict:
    """Parse a JSON object, tolerating markdown fences some models add."""
    s = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if m:
        s = m.group(1).strip()
    if not s.startswith("{"):
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            s = s[i:j + 1]
    data = json.loads(s)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data
