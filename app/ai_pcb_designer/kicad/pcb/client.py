"""Host-side wrapper around the pcbnew worker subprocess."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ..cli.runner import run

WORKER = Path(__file__).with_name("worker.py")


class PcbWorkerError(RuntimeError):
    pass


class PcbWorker:
    def __init__(self, kicad_python: str, env: dict | None = None):
        self.python = kicad_python
        self.env = env or {}

    def call(self, command: str, job: dict, timeout: float = 300) -> dict:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(job, fh)
            job_path = fh.name
        res = run([self.python, str(WORKER), command, job_path], check=False, timeout=timeout, env=self.env)
        Path(job_path).unlink(missing_ok=True)
        lines = [l for l in res.stdout.strip().splitlines() if l.startswith("{")]
        if not lines:
            raise PcbWorkerError(f"{command}: no result (rc={res.returncode}) {res.stderr[-1500:]}")
        payload = json.loads(lines[-1])
        if not payload.get("ok"):
            raise PcbWorkerError(f"{command}: {payload.get('error')}\n{payload.get('trace', '')}")
        return payload["result"]
