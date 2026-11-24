"""Structured logging: JSON-lines file (rotating), in-memory ring for the log panel,
and a filter that redacts anything that looks like an API key."""
from __future__ import annotations

import collections
import json
import logging
import logging.handlers
import re
import threading
import time

from .config import data_dir

_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|AIza[0-9A-Za-z_\-]{20,}|Bearer\s+[A-Za-z0-9._\-]{8,}|"
                     r"(api[_-]?key|x-goog-api-key)\W+[A-Za-z0-9._\-]{8,})", re.I)


def redact(text: str) -> str:
    return _SECRET.sub("[REDACTED]", text)


class RedactFilter(logging.Filter):
    def filter(self, record):
        # format first (numeric %-formats need the original arg types), then redact the text
        try:
            msg = record.getMessage()
        except Exception:
            msg = f"{record.msg} {record.args}"
        record.msg, record.args = redact(msg), None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, r):
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(r.created)), "level": r.levelname,
             "logger": r.name, "msg": r.getMessage()}
        if r.exc_info:
            d["exc"] = redact(self.formatException(r.exc_info))
        for k in ("event", "project", "step"):
            if hasattr(r, k):
                d[k] = getattr(r, k)
        return json.dumps(d)


class RingHandler(logging.Handler):
    """Keeps recent records for the UI log panel; listeners are called from any thread."""

    def __init__(self, size=2000):
        super().__init__()
        self.records = collections.deque(maxlen=size)
        self.listeners = []
        self._lock = threading.Lock()

    def emit(self, record):
        line = f"{time.strftime('%H:%M:%S', time.localtime(record.created))} {record.levelname:7s} " \
               f"{record.name}: {record.getMessage()}"
        with self._lock:
            self.records.append(line)
        for fn in list(self.listeners):
            try:
                fn(line)
            except Exception:
                pass


RING = RingHandler()


def setup_logging(level=logging.INFO):
    logdir = data_dir() / "logs"
    logdir.mkdir(exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = logging.handlers.RotatingFileHandler(logdir / "app.jsonl", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    for h in (fh, RING):
        h.addFilter(RedactFilter())
        root.addHandler(h)
    sh = logging.StreamHandler()
    sh.setLevel(logging.WARNING)
    sh.addFilter(RedactFilter())
    root.addHandler(sh)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logdir
