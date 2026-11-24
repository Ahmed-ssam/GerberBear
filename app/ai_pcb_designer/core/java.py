"""Locate a Java runtime (11+) for Freerouting.

Order: a JRE bundled next to the app (tools/jre), JAVA_HOME, PATH, then the usual
Windows/macOS install folders (Temurin, Microsoft, Oracle, Zulu)."""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from pathlib import Path

from .config import package_root

IS_WINDOWS = os.name == "nt"
EXE = "java.exe" if IS_WINDOWS else "java"


def _candidates() -> list[str]:
    out = [str(package_root().parent / "tools" / "jre" / "bin" / EXE)]
    if os.environ.get("JAVA_HOME"):
        out.append(str(Path(os.environ["JAVA_HOME"]) / "bin" / EXE))
    w = shutil.which("java")
    if w:
        out.append(w)
    if IS_WINDOWS:
        for base in {os.environ.get("ProgramFiles", r"C:\Program Files"), r"C:\Program Files"}:
            for vendor in ("Eclipse Adoptium", "Microsoft", "Java", "Zulu", "Amazon Corretto", "BellSoft"):
                out += sorted(glob.glob(str(Path(base) / vendor / "*" / "bin" / EXE)), reverse=True)
    else:
        out += sorted(glob.glob("/Library/Java/JavaVirtualMachines/*/Contents/Home/bin/java"), reverse=True)
    return list(dict.fromkeys(out))


def java_version(exe: str) -> int | None:
    try:
        kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}
        r = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace", **kw)
    except Exception:
        return None
    m = re.search(r'version "(\d+)(?:\.(\d+))?', r.stderr + r.stdout)
    if not m:
        return None
    major = int(m.group(1))
    return int(m.group(2) or 0) if major == 1 else major      # "1.8" -> 8


def find_java(min_major: int = 11) -> tuple[str | None, int | None]:
    """Return (path, major) of the first Java >= min_major, else (None, best_found_major)."""
    best = None
    for c in _candidates():
        if not Path(c).exists():
            continue
        v = java_version(c)
        if v and v >= min_major:
            return c, v
        best = v or best
    return None, best
