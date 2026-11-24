"""Diagnostic report for bug reports: a zip with the app log, the installation check,
system facts and the current revision's run record - never API keys or settings secrets
(keys live in the OS keychain and are not in any of these files; the log is redacted)."""
from __future__ import annotations

import json
import platform
import sys
import time
import zipfile
from pathlib import Path

from .config import data_dir
from .version import __version__


def build(dest: Path, caps=None, rev_dir: Path | None = None) -> Path:
    info = {"app_version": __version__, "python": sys.version, "platform": platform.platform(),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "kicad": getattr(caps, "version", None), "kicad_cli": getattr(caps, "cli_path", None)}
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("system.json", json.dumps(info, indent=1))
        logs = data_dir() / "logs"
        for f in sorted(logs.glob("app.jsonl*"))[:3]:
            z.write(f, f"logs/{f.name}")
        if rev_dir:
            rev_dir = Path(rev_dir)
            for name in ("run.json", "proposal.json"):
                if (rev_dir / name).exists():
                    z.write(rev_dir / name, f"revision/{name}")
            for f in sorted((rev_dir / "reports").glob("*"))[:10] if (rev_dir / "reports").is_dir() else []:
                z.write(f, f"revision/reports/{f.name}")
    return dest
