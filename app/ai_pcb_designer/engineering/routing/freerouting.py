"""Autorouting through Freerouting (Specctra DSN in, SES out).

KiCad exports the board as DSN (real nets, pads, net-class widths, clearances),
Freerouting routes it, and KiCad imports the session back. Completeness is
judged afterwards from KiCad's own ratsnest, not from Freerouting's log.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ...kicad.cli.runner import run


@dataclass
class RoutingResult:
    ok: bool
    ses_path: Path | None
    passes: int | None = None
    log_tail: list[str] = field(default_factory=list)
    error: str | None = None
    seconds: float = 0.0
    last_pass_unrouted: int | None = None


def freerouting_major(jar: Path) -> int:
    m = re.search(r"freerouting-(\d+)\.", jar.name)
    return int(m.group(1)) if m else 1


def route(jar: Path, dsn: Path, ses: Path, *, max_passes: int = 40, timeout: float = 1800,
          threads: int = 2, optimize: str = "standard") -> RoutingResult:
    """Run Freerouting headless.

    1.9.x (default): honours -mp, makes no network calls, needs a display -> xvfb-run when
    none is present. 2.1.x: ignored the pass limit and phoned home in our tests; kept for
    comparison with its own flag set."""
    ses.unlink(missing_ok=True)
    from ...core.java import find_java
    java, _ = find_java()
    if not java:
        return RoutingResult(False, None, None, [], "Java 11 or newer not found (install Temurin 21 JRE)")
    if freerouting_major(jar) >= 2:
        argv = [java, "-Djava.awt.headless=true", "-jar", str(jar), "-de", str(dsn), "-do", str(ses),
                "-mp", str(max_passes), f"--router.max_passes={max_passes}", "--gui.enabled=false",
                "--profile.allow_telemetry=false", "--usage_and_diagnostic_data.disable_analytics=true",
                "--router.optimizer.max_passes=10", f"--router.max_threads={threads}"]
    else:
        # Optimiser: single thread only (1.9 warns multi-threaded optimisation causes clearance
        # violations); stop when a pass improves < 2 % ("standard"), or skip it ("off").
        opt = ["-mt", "0"] if optimize == "off" else ["-mt", "1", "-oit", "2"]
        argv = [java, "-jar", str(jar), "-de", str(dsn), "-do", str(ses), "-mp", str(max_passes), *opt]
        # Linux servers without a display need a virtual one; Windows/macOS desktops have one.
        if os.name != "nt" and not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
            argv = ["xvfb-run", "-a"] + argv
    res = run(argv, check=False, timeout=timeout, background_window=True)
    lines = [l for l in (res.stdout + res.stderr).splitlines()
             if l and "JAVA_TOOL_OPTIONS" not in l and "_JAVA_OPTIONS" not in l and not l.startswith("\tat ")]
    passes, unrouted = None, None
    for l in lines:
        m = re.search(r"pass #(\d+)", l, re.I)
        if m:
            passes = int(m.group(1))
            u = re.search(r"\((\d+) unrouted\)", l)
            unrouted = int(u.group(1)) if u else 0
    if not ses.exists():
        return RoutingResult(False, None, passes, lines[-15:], f"Freerouting produced no SES (rc={res.returncode})",
                             seconds=res.duration_s)
    return RoutingResult(True, ses, passes, lines[-15:], seconds=res.duration_s, last_pass_unrouted=unrouted)


LOGIC_RAILS = ("+3V3", "+3.3V", "+5V", "+1V8", "+2V5", "+3V0")


def narrow_dsn_class(dsn: Path, nets: list[str], width_um: float, cls: str = "Power") -> list[str]:
    """Move `nets` out of netclass `cls` into a new class with a narrower track width.
    Used only as a last routing fallback for low-current logic rails; returns the nets
    actually moved (empty if none were in the class)."""
    text = dsn.read_text(encoding="utf-8")
    start = text.find(f"(class {cls} ")
    if start < 0:
        return []
    depth, i = 0, start
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    block = text[start:i + 1]
    head_end = block.find("(circuit")
    head, rest = block[:head_end], block[head_end:]
    tokens = re.findall(r'"[^"]*"|\S+', head)[2:]            # skip "(class" and name
    moved = [t for t in tokens if t.strip('"') in nets]
    if not moved:
        return []
    kept = [t for t in tokens if t not in moved]
    new_head = f"(class {cls} " + " ".join(kept) + "\n      "
    narrow = re.sub(r"\(width\s+[\d.]+\)", f"(width {width_um:g})", rest)
    new_block = new_head + rest + "\n    " + f"(class {cls}_narrow " + " ".join(moved) + "\n      " + narrow
    dsn.write_text(text[:start] + new_block + text[i + 1:], encoding="utf-8")
    return [m.strip('"') for m in moved]
