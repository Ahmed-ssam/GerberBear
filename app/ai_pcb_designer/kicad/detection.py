"""Detect the installed KiCad and what can actually be automated with it.

Capabilities are derived from the detected version *and* confirmed by probing
(CLI subcommands, Python binding symbols), never assumed.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .cli.runner import run

IS_WINDOWS = os.name == "nt"
WIN_VERSIONS = ("10.0", "9.0", "8.0")          # newest first; KiCad installs side by side


def _win_roots() -> list[Path]:
    roots = []
    for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if not base:
            continue
        for v in WIN_VERSIONS:
            for sub in (Path(base) / "KiCad" / v, Path(base) / "Programs" / "KiCad" / v):
                if sub not in roots:
                    roots.append(sub)
    for v in WIN_VERSIONS:                      # default location even if env is unusual
        p = Path(rf"C:\Program Files\KiCad\{v}")
        if p not in roots:
            roots.append(p)
    return roots


def find_cli() -> str | None:
    if IS_WINDOWS:
        for root in _win_roots():
            exe = root / "bin" / "kicad-cli.exe"
            if exe.exists():
                return str(exe)
    found = shutil.which("kicad-cli")
    if found:
        return found
    mac = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
    return str(mac) if mac.exists() else None


def python_candidates(cli: str | None) -> list[str]:
    """KiCad's own interpreter first: it is the one built against the installed pcbnew."""
    out = []
    if cli:
        b = Path(cli).parent
        out += [str(b / "python.exe"), str(b / "python3"),
                str(b.parent / "Frameworks/Python.framework/Versions/Current/bin/python3")]
    out += ["/usr/bin/python3.12", "/usr/bin/python3", "/usr/bin/python3.11",
            "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"]
    out += [str(r / "bin" / "python.exe") for r in (_win_roots() if IS_WINDOWS else [])]
    return list(dict.fromkeys(out))


def share_candidates(cli: str | None) -> list[str]:
    out = []
    if cli:
        b = Path(cli).parent
        out += [str(b.parent / "share" / "kicad"), str(b.parent / "SharedSupport")]
    out += ["/usr/share/kicad", "/usr/local/share/kicad", "/Applications/KiCad/KiCad.app/Contents/SharedSupport"]
    return list(dict.fromkeys(out))


# kept for callers/tests that import the old names
KICAD_PYTHON_CANDIDATES = python_candidates(None)
SHARE_CANDIDATES = share_candidates(None)


@dataclass
class KiCadCapabilities:
    installed: bool = False
    version: str | None = None
    major: int | None = None
    cli_path: str | None = None
    python_path: str | None = None          # interpreter that can `import pcbnew`
    pcbnew_version: str | None = None
    share_dir: str | None = None
    symbol_dir: str | None = None
    footprint_dir: str | None = None
    models3d_dir: str | None = None
    pcb_export_cmds: list[str] = field(default_factory=list)
    sch_export_cmds: list[str] = field(default_factory=list)
    cli_drc: bool = False                   # kicad-cli pcb drc  (KiCad >= 8)
    cli_erc: bool = False                   # kicad-cli sch erc  (KiCad >= 8)
    cli_glb: bool = False                   # kicad-cli pcb export glb (KiCad >= 9)
    api_drc: bool = False                   # pcbnew.WriteDRCReport
    api_specctra: bool = False              # ExportSpecctraDSN / ImportSpecctraSES
    ipc_api: bool = False                   # KiCad >= 9 IPC API (needs running KiCad)
    cli_ipc2581: bool = False               # kicad-cli pcb export ipc2581 (KiCad >= 8)
    cli_odb: bool = False                   # kicad-cli pcb export odb (KiCad >= 9)
    notes: list[str] = field(default_factory=list)

    def path_env(self) -> dict[str, str]:
        """KiCad path variables (KICADn_*_DIR) that libraries and tables refer to."""
        if not self.major:
            return {}
        env = {}
        # Libraries shipped with KiCad N may still reference older variables (e.g. KICAD9_*
        # inside KiCad 10 footprints), so define every version up to the installed one.
        for major in range(6, self.major + 1):
            v = f"KICAD{major}"
            for key, val in ((f"{v}_SYMBOL_DIR", self.symbol_dir), (f"{v}_FOOTPRINT_DIR", self.footprint_dir),
                             (f"{v}_3DMODEL_DIR", self.models3d_dir)):
                if val:
                    env[key] = val
        return env

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def _subcommands(help_text: str) -> list[str]:
    m = re.search(r"\{([^}]*)\}", help_text)
    return [s.strip() for s in m.group(1).split(",")] if m else []


def detect(cli_override: str | None = None) -> KiCadCapabilities:
    caps = KiCadCapabilities()
    cli = cli_override or find_cli()
    if not cli:
        caps.notes.append("kicad-cli not found (looked in Program Files\\KiCad\\10.0/9.0/8.0 and PATH); "
                          "KiCad integration unavailable.")
        return caps
    caps.cli_path = cli
    caps.version = run([cli, "version"]).stdout.strip().splitlines()[-1].strip()
    caps.installed = True
    m = re.search(r"(\d+)\.(\d+)", caps.version)
    caps.major = int(m.group(1)) if m else 0

    pcb_root = run([cli, "pcb", "--help"], check=False).stdout
    sch_root = run([cli, "sch", "--help"], check=False).stdout
    caps.pcb_export_cmds = _subcommands(run([cli, "pcb", "export", "--help"], check=False).stdout)
    caps.sch_export_cmds = _subcommands(run([cli, "sch", "export", "--help"], check=False).stdout)
    caps.cli_drc = "drc" in _subcommands(pcb_root)
    caps.cli_erc = "erc" in _subcommands(sch_root)
    caps.cli_glb = "glb" in caps.pcb_export_cmds
    caps.cli_ipc2581 = "ipc2581" in caps.pcb_export_cmds
    caps.cli_odb = "odb" in caps.pcb_export_cmds

    probe = ("import pcbnew,json;print(json.dumps({'v':pcbnew.Version(),"
             "'drc':hasattr(pcbnew,'WriteDRCReport'),"
             "'dsn':hasattr(pcbnew,'ExportSpecctraDSN') and hasattr(pcbnew,'ImportSpecctraSES')}))")
    for py in python_candidates(cli):
        if not Path(py).exists():
            continue
        try:
            r = run([py, "-c", probe], check=False, timeout=90)
        except (PermissionError, FileNotFoundError):
            continue
        if r.ok:
            info = json.loads(r.stdout.strip().splitlines()[-1])
            caps.python_path, caps.pcbnew_version = py, info["v"]
            caps.api_drc, caps.api_specctra = info["drc"], info["dsn"]
            break
    if not caps.python_path:
        caps.notes.append("No interpreter could import pcbnew; PCB build via API unavailable.")

    for d in share_candidates(cli):
        p = Path(d)
        if (p / "symbols").is_dir():
            caps.share_dir = str(p)
            caps.symbol_dir = str(p / "symbols")
            caps.footprint_dir = str(p / "footprints") if (p / "footprints").is_dir() else None
            m3 = p / "3dmodels"
            caps.models3d_dir = str(m3) if m3.is_dir() and any(m3.iterdir()) else None
            break
    if not caps.models3d_dir:
        caps.notes.append("3D model library not installed; STEP/GLB will show bare footprints only.")

    caps.ipc_api = caps.major >= 9
    if caps.major < 8:
        caps.notes.append("KiCad < 8: no CLI DRC/ERC; DRC runs through pcbnew.WriteDRCReport.")
    if caps.major < 9:
        caps.notes.append("KiCad < 9: no IPC API and no CLI GLB export; GLB produced from STEP.")
    return caps


if __name__ == "__main__":
    print(detect().to_json())
