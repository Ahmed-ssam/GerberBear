"""Controlled subprocess execution for KiCad and helper tools.

Only executables on an allow-list may be run, arguments are passed as a list
(never through a shell), and every call is logged with its result. The AI layer
never reaches this module directly; it goes through engineering tools.
"""
from __future__ import annotations

import contextvars
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

ALLOWED_EXECUTABLES = {"kicad-cli", "java", "python3.12", "python3", "xvfb-run",
                       "python.exe", "kicad-cli.exe", "java.exe", "javaw.exe", "python3.11", "python"}
IS_WINDOWS = os.name == "nt"


class Cancelled(RuntimeError):
    """The user cancelled the running job."""


class CancelToken:
    def __init__(self):
        self._ev = threading.Event()

    def cancel(self):
        self._ev.set()

    @property
    def cancelled(self) -> bool:
        return self._ev.is_set()

    def check(self):
        if self._ev.is_set():
            raise Cancelled("cancelled by user")


# The job runner sets this for the duration of a job; every subprocess started in that
# context is killed (with its whole process group) when the token is cancelled.
current_cancel: contextvars.ContextVar[CancelToken | None] = contextvars.ContextVar("current_cancel", default=None)


class ExecutionError(RuntimeError):
    def __init__(self, result: "RunResult"):
        super().__init__(
            f"{result.argv[0]} exited with {result.returncode}: {result.stderr.strip()[-800:]}"
        )
        self.result = result


@dataclass
class RunResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    extra: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(argv: list[str], *, cwd: Path | None = None, timeout: float = 300,
        check: bool = True, env: dict | None = None, background_window: bool = False) -> RunResult:
    exe = Path(argv[0]).name
    if exe.lower() not in ALLOWED_EXECUTABLES:
        raise PermissionError(f"Executable not allowed: {exe}")
    resolved = argv[0] if os.path.isabs(argv[0]) else shutil.which(argv[0])
    if not resolved:
        raise FileNotFoundError(f"Executable not found: {argv[0]}")
    # UTF-8 everywhere: Windows defaults to the ANSI code page, which corrupts KiCad files
    # and JSON that contain non-ASCII text (µF, Ω, user names in paths).
    full_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", **(env or {})}
    token = current_cancel.get()
    if token:
        token.check()
    start = time.monotonic()
    if IS_WINDOWS:
        group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
        if background_window:
            # GUI tools that insist on a window (Freerouting 1.9): start it minimised and
            # without focus so users cannot click it (a click stops its optimiser).
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 7                      # SW_SHOWMINNOACTIVE
            group["startupinfo"] = si
    else:
        group = {"start_new_session": True}
    proc = subprocess.Popen([resolved, *argv[1:]], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                            env=full_env, shell=False, **group)
    out_err: dict = {}
    reader = threading.Thread(target=lambda: out_err.update(zip(("out", "err"), proc.communicate())), daemon=True)
    reader.start()
    while reader.is_alive():
        reader.join(0.2)
        elapsed = time.monotonic() - start
        if (token and token.cancelled) or elapsed > timeout:
            _kill_group(proc)
            reader.join(5)
            if token and token.cancelled:
                raise Cancelled(f"cancelled while running {exe}")
            raise subprocess.TimeoutExpired(argv, timeout)
    res = RunResult([resolved, *argv[1:]], proc.returncode, out_err.get("out", ""), out_err.get("err", ""),
                    time.monotonic() - start)
    log.info("run %s rc=%s %.2fs", " ".join(argv[:3]), res.returncode, res.duration_s)
    if check and not res.ok:
        raise ExecutionError(res)
    return res


def _kill_group(proc: subprocess.Popen):
    if IS_WINDOWS:
        # Kill the whole tree (java/kicad-cli may spawn children).
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            proc.wait(5)
        except Exception:
            proc.kill()
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(3)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass
