"""Supplier part numbers (LCSC / JLCPCB) from a live catalogue search.

Uses the public jlcsearch API (an in-stock index of JLCPCB's parts library, no key). A
result is accepted only when the catalogue's own description confirms the part: the same
resistance / capacitance (and at least the needed voltage rating) and the same package for
passives, or a manufacturer part number that starts with the value for named parts.
Anything not confirmed stays blank and is listed for manual sourcing - a wrong part on an
assembled board is worse than a blank line. Results are cached for 7 days.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

API = "https://jlcsearch.tscircuit.com/api/search"
CACHE_DAYS = 7
PKG = re.compile(r"_(0402|0603|0805|1206|1210|1812)_")

# parts whose catalogue entry is matched by manufacturer part number (value = MPN)
NAMED = re.compile(r"^(AO3400A|AO3401A|DMG2301L|SS14|SS34|1N4148W|MMBT3904|SMAJ\d+C?A|AP63203WU|AP63205WU|"
                   r"AP2112K-3\.3|AMS1117-3\.3|MCP73831-2-OT|DRV8871DDA|PCA9685PW|CH340C|MAX3485|SN65HVD230|"
                   r"SHT31-DIS|BME280|ADS1115|DS18B20|USBLC6-2SC6|ESP32-WROOM-32)$")
MPN_SEARCH = {"ADS1115": "ADS1115IDGSR", "SHT31-DIS": "SHT31-DIS-B", "AMS1117-3.3": "AMS1117-3.3",
              "MAX3485": "MAX3485ESA", "SN65HVD230": "SN65HVD230DR", "PCA9685PW": "PCA9685PW",
              "ESP32-WROOM-32": "ESP32-WROOM-32", "DS18B20": "DS18B20"}


def _norm_value(v: str) -> str:
    """'4.7k' -> '4.7kΩ', '100' -> '100Ω', '100nF 25V' -> '100nF'."""
    v = v.split()[0]
    if re.fullmatch(r"\d+(\.\d+)?[kKmM]?", v):
        return v.replace("K", "k") + "Ω"
    return v.replace("u", "µ")


def _cap_ok(desc: str, value: str) -> bool:
    want = _norm_value(value)
    vals = [want, want.replace("µ", "u")]
    if not any(re.search(rf"(?<![\d.]){re.escape(x)}(?![\d])", desc) for x in vals):
        return False
    need = re.search(r"(\d+(?:\.\d+)?)V", value)
    if need:
        got = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*V\b", desc)]
        return any(g >= float(need.group(1)) for g in got)
    return True


def _res_ok(desc: str, value: str) -> bool:
    want = _norm_value(value)
    return re.search(rf"(?<![\d.]){re.escape(want)}", desc) is not None


class Sourcing:
    def __init__(self, cache_file: Path, timeout: float = 8.0, budget_s: float = 90.0):
        self.cache_file, self.timeout, self.deadline = cache_file, timeout, time.monotonic() + budget_s
        try:
            self.cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}
        self.errors: list[str] = []
        self.client = None

    def _search(self, q: str, package: str | None) -> list[dict]:
        key = f"{q}|{package}"
        hit = self.cache.get(key)
        if hit and time.time() - hit["t"] < CACHE_DAYS * 86400:
            return hit["r"]
        if time.monotonic() > self.deadline or len(self.errors) >= 3:
            return []
        import httpx
        try:
            if self.client is None:
                self.client = httpx.Client(timeout=self.timeout)
            params = {"q": q, "limit": 30, "full": "true"}
            if package:
                params["package"] = package
            r = self.client.get(API, params=params)
            r.raise_for_status()
            comps = r.json().get("components") or []
        except Exception as e:                                   # offline, blocked, changed API
            self.errors.append(f"{type(e).__name__}: {str(e)[:120]}")
            return []
        self.cache[key] = {"t": time.time(), "r": comps}
        return comps

    def find(self, symbol: str, value: str, footprint: str) -> dict | None:
        """{'lcsc': 'C25804', 'mfr': ..., 'description': ..., 'basic': bool, 'stock': n} or None."""
        pkg = PKG.search(footprint + "_")
        pkg = pkg.group(1) if pkg else None
        if symbol in ("Device:R",) and pkg:
            cands = self._search(f"{_norm_value(value)} {pkg} resistor", pkg)
            ok = [c for c in cands if _res_ok(str(c.get("description", "")), value)]
        elif symbol in ("Device:C",) and pkg:
            cands = self._search(f"{_norm_value(value)} {pkg} capacitor", pkg)
            ok = [c for c in cands if _cap_ok(str(c.get("description", "")), value)]
        elif symbol == "Device:LED" and pkg:
            cands = self._search(f"{value.lower()} LED {pkg}", pkg)
            ok = [c for c in cands if value.lower() in str(c.get("description", "")).lower()]
        elif NAMED.match(value):
            cands = self._search(MPN_SEARCH.get(value, value), None)
            base = value.split("-OT")[0] if value.startswith("MCP73831") else value
            ok = [c for c in cands if str(c.get("mfr", "")).upper().startswith(base.upper())]
        else:
            return None
        ok = [c for c in ok if c.get("lcsc") and int(c.get("stock") or 0) > 0]
        if not ok:
            return None
        ok.sort(key=lambda c: (not c.get("is_basic"), not c.get("is_preferred"), -int(c.get("stock") or 0)))
        c = ok[0]
        lcsc = str(c["lcsc"])
        return {"lcsc": lcsc if lcsc.startswith("C") else f"C{lcsc}", "mfr": c.get("mfr"),
                "description": c.get("description"), "basic": bool(c.get("is_basic")),
                "stock": int(c.get("stock") or 0)}

    def save(self):
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps(self.cache), encoding="utf-8")
        except OSError:
            pass
