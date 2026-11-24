"""Application paths and user settings (JSON). Secrets are never stored here."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_ID = "AIPCBDesigner"


def data_dir() -> Path:
    if os.environ.get("AIPCB_HOME"):
        p = Path(os.environ["AIPCB_HOME"])
    elif sys.platform == "win32":
        p = Path(os.environ.get("APPDATA", Path.home())) / APP_ID
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / APP_ID
    else:
        p = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_ID
    p.mkdir(parents=True, exist_ok=True)
    return p


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


@dataclass
class AISettings:
    provider: str = "openai"                 # openai | gemini | openai_compatible | scripted (dev only)
    model: str = "gpt-4.1-mini"
    base_url: str = ""
    temperature: float = 0.2
    explain_results: bool = True


@dataclass
class KiCadSettings:
    cli_path: str = ""                       # empty = auto-detect
    freerouting_jar: str = ""                # empty = bundled tools/freerouting-1.9.0.jar
    routing_profile: str = "standard"        # standard | fast


@dataclass
class UISettings:
    theme: str = "dark"
    software_3d: bool = False                # force SwiftShader WebGL (VMs / remote desktops)
    show_log_panel: bool = False
    reduce_motion: bool = False              # animations off (accessibility, slow remote desktops)
    chat_width: int = 380


@dataclass
class EngineeringSettings:
    default_layers: str = "auto"             # auto | 2 | 4
    units: str = "mm"
    clearance_mm: float = 0.2
    track_width_mm: float = 0.25


@dataclass
class Settings:
    ai: AISettings = field(default_factory=AISettings)
    kicad: KiCadSettings = field(default_factory=KiCadSettings)
    ui: UISettings = field(default_factory=UISettings)
    engineering: EngineeringSettings = field(default_factory=EngineeringSettings)

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or data_dir() / "settings.json"
        s = cls()
        if path.exists():
            try:
                raw = json.loads(path.read_text())
                for section in ("ai", "kicad", "ui", "engineering"):
                    obj = getattr(s, section)
                    for k, v in (raw.get(section) or {}).items():
                        if hasattr(obj, k):
                            setattr(obj, k, v)
            except (ValueError, OSError):
                pass                             # corrupt settings -> defaults (logged by caller)
        return s

    def save(self, path: Path | None = None):
        path = path or data_dir() / "settings.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        tmp.replace(path)


def freerouting_jar(settings: Settings) -> Path:
    if settings.kicad.freerouting_jar:
        return Path(settings.kicad.freerouting_jar)
    for cand in (package_root().parent / "tools" / "freerouting-1.9.0.jar", data_dir() / "tools" / "freerouting-1.9.0.jar"):
        if cand.exists():
            return cand
    return package_root().parent / "tools" / "freerouting-1.9.0.jar"
