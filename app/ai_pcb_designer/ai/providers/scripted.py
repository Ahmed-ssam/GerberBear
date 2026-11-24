"""SIMULATED AI - development and testing only.

A keyword-based stand-in for an LLM so the complete desktop workflow can be exercised
without an API key (CI, screenshots, offline development). It is NOT an AI: it only
recognises simple phrasings ("12V", "8 outputs", "0.5A", "esp32", "fan", "go ahead").

It is disabled unless the environment variable AIPCB_DEV_SCRIPTED_AI=1 is set, and the
UI shows a permanent "SIMULATED AI" badge whenever it is active.
"""
from __future__ import annotations

import json
import os
import re

from .base import LLMProvider, Message, ProviderConfig


def scripted_allowed() -> bool:
    return os.environ.get("AIPCB_DEV_SCRIPTED_AI") == "1"


class ScriptedDevProvider(LLMProvider):
    name = "scripted"
    simulated = True

    def __init__(self, cfg: ProviderConfig, transport=None):
        super().__init__(cfg, transport)

    def list_models(self) -> list[str]:
        return ["simulated"]

    def _chat(self, system: str, messages: list[Message], json_mode: bool) -> str:
        last = messages[-1].content
        if system.startswith("You are the requirements extractor"):
            return json.dumps(self._extract(last.split("\n\n---\n")[0], last))
        state = json.loads(last.split("STATE:\n", 1)[1].rsplit("\nWrite your reply", 1)[0])
        return self._reply(state)

    # -------------------------------------------------------- extraction
    @staticmethod
    def _extract(user: str, full: str) -> dict:
        t = user.lower()
        stage = re.search(r"CURRENT_STAGE: (\w+)", full)
        stage = stage.group(1) if stage else "gathering"
        if re.search(r"\b(approve|go ahead|looks good|generate it|build it|yes,? generate)\b", t) and stage == "proposal":
            return {"intent": "approve", "spec_updates": {}, "user_question": None}
        if re.search(r"\b(reject|start over|discard)\b", t):
            return {"intent": "reject", "spec_updates": {}, "user_question": None}
        u: dict = {}
        loads: dict = {}
        if "esp32" in t or "wifi" in t or "wi-fi" in t:
            u["controller"] = "esp32"
        if re.search(r"\b(i2c|external (controller|host)|raspberry|arduino)\b", t):
            u["controller"] = "external_i2c"
        m = re.search(r"(\d+(?:\.\d+)?)\s*v(?:olts?)?\b", t)
        if m:
            u["input_voltage"] = float(m.group(1))
            loads["voltage"] = float(m.group(1))
        m = re.search(r"(\d+)\s*(?:x\s*)?(?:channels?|outputs?|loads?|fans?|strips?|solenoids?|valves?)\b", t)
        if m:
            loads["count"] = int(m.group(1))
        elif re.search(r"\b(a|one|single) (fan|output|load|solenoid|valve)\b", t):
            loads["count"] = 1
        m = re.search(r"(\d+(?:\.\d+)?)\s*(m?a)(?:mps?)?\b", t)
        if m:
            loads["current_per_channel_a"] = float(m.group(1)) / (1000 if m.group(2) == "ma" else 1)
        for word, kind in (("fan", "dc_fan"), ("led strip", "led_strip"), ("solenoid", "solenoid"),
                           ("valve", "solenoid"), ("relay", "relay_coil"), ("motor", "motor_dc"),
                           ("heater", "heater"), ("lamp", "lamp")):
            if word in t:
                loads["kind"] = kind
                break
        sensors = []
        if "ds18b20" in t or ("temperature" in t and "humidity" not in t and u.get("controller") != "external_i2c"):
            sensors.append({"kind": "ds18b20"})
        if "humidity" in t or "sht31" in t:
            sensors.append({"kind": "sht31"})
        if "adc" in t or "analog" in t or "ads1115" in t:
            sensors.append({"kind": "ads1115"})
        if sensors:
            cur = re.search(r"CURRENT_SPEC: (\{.*\})", full)
            if cur and re.search(r"\b(add|also|too|plus|as well)\b", t):   # additive phrasing keeps existing ones
                existing = json.loads(cur.group(1)).get("sensors", [])
                sensors = existing + [x for x in sensors if x not in existing]
            u["sensors"] = sensors
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:x|by)\s*(\d+(?:\.\d+)?)\s*mm", t)
        if m:
            u["board_width_mm"], u["board_height_mm"] = float(m.group(1)), float(m.group(2))
        m = re.search(r"\b([24])[\s-]*layers?\b", t)
        if m:
            u["layers"] = int(m.group(1))
        m = re.search(r"\b(no|two|2|four|4)\s+mounting holes?\b", t)
        if m:
            u["mounting_holes"] = {"no": 0, "two": 2, "2": 2, "four": 4, "4": 4}[m.group(1)]
        if "usb" in t:
            u["unsupported_requests"] = ["USB"]
        if loads:
            u["loads"] = loads
        intent = "modify" if stage in ("proposal", "generated") and u else ("describe" if u else "question")
        return {"intent": intent, "spec_updates": u, "user_question": None if u else user}

    # ------------------------------------------------------------- reply
    @staticmethod
    def _reply(st: dict) -> str:
        an, stage = st.get("analysis") or {}, st["stage"]
        if stage == "generated" and st.get("results"):
            r = st["results"]
            drc = r.get("drc") or {}
            return (f"[Simulated AI] The board is generated. Routing complete: {r.get('routing_complete')}, "
                    f"unrouted connections: {r.get('unrouted')}, DRC errors: {drc.get('errors')}, "
                    f"warnings: {drc.get('warnings')}. Status: {r.get('validation_status')}.")
        if stage == "proposal":
            p = st.get("proposal") or {}
            return (f"[Simulated AI] {p.get('overview', '')} The proposal below lists the architecture, parts and "
                    "assumptions. Approve it to generate the schematic and PCB, or tell me what to change.")
        parts = []
        for c in an.get("conflicts", []) + an.get("unsupported", []):
            parts.append(c)
        qs = [q for q in an.get("missing", []) if q["tier"] == "critical"][:3] or \
             [q for q in an.get("missing", []) if q["tier"] == "recommended"][:2]
        for q in qs:
            parts.append(f"- **{q['field']}**: {q['why']}")
        if st.get("user_question") and not qs:
            parts.append("I can only answer design questions with a real AI provider configured.")
        return "[Simulated AI] " + ("\n".join(parts) if parts else "Noted.")
