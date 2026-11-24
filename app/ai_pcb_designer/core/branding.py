"""Replaceable branding: name, tagline, colours and logo come from resources/branding/brand.json."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .config import package_root

BRAND_DIR = package_root() / "resources" / "branding"


@dataclass
class Branding:
    name: str = "AI PCB Designer"
    tagline: str = "AI-powered PCB design"
    logo_svg: str = "logo.svg"
    icon: str = "gerber_bear.ico"
    wordmark: list = field(default_factory=lambda: ["AI PCB", "DESIGNER"])
    colors: dict = field(default_factory=lambda: {
        "bg": "#0A1020", "bg2": "#0D1528", "surface": "#0F172A", "surface2": "#141F36", "surface3": "#1A2742",
        "border": "#1F2C47", "border2": "#2A3B5E", "text": "#E8EEF8", "muted": "#8693AD", "dim": "#5B6883",
        "accent": "#06B6D4", "accent2": "#22D3EE", "gold": "#FFD700", "gold2": "#F5C542", "copper": "#D9822B",
        "success": "#22C55E", "warning": "#F59E0B", "danger": "#EF4444"})
    fonts: dict = field(default_factory=lambda: {"ui": "Inter", "mono": "Roboto Mono"})

    @property
    def logo_path(self):
        return BRAND_DIR / self.logo_svg

    @property
    def icon_path(self):
        p = BRAND_DIR / self.icon
        return p if p.exists() else self.logo_path

    def c(self, key: str) -> str:
        return self.colors.get(key, "#FF00FF")

    @classmethod
    def load(cls) -> "Branding":
        b = cls()
        f = BRAND_DIR / "brand.json"
        if f.exists():
            raw = json.loads(f.read_text(encoding="utf-8"))
            b.name = raw.get("name", b.name)
            b.tagline = raw.get("tagline", b.tagline)
            b.logo_svg = raw.get("logo_svg", b.logo_svg)
            b.icon = raw.get("icon", b.icon)
            b.wordmark = raw.get("wordmark", b.wordmark)
            b.colors.update(raw.get("colors", {}))
            b.fonts.update(raw.get("fonts", {}))
        return b
