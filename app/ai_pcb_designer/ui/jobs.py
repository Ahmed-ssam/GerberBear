"""Background jobs on a QThreadPool. The UI thread never runs network calls or KiCad.

Each job gets its own CancelToken (installed into the worker thread's context), so
cancel() stops between pipeline steps and kills any running subprocess group.
"""
from __future__ import annotations

import logging
import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..kicad.cli.runner import CancelToken, Cancelled, current_cancel

log = logging.getLogger(__name__)


class JobSignals(QObject):
    progress = Signal(dict)
    finished = Signal(object)
    failed = Signal(str, str)        # user message, technical trace
    cancelled = Signal()


class Job(QRunnable):
    def __init__(self, name: str, fn, *args, **kwargs):
        super().__init__()
        self.name, self.fn, self.args, self.kwargs = name, fn, args, kwargs
        self.signals = JobSignals()
        self.token = CancelToken()
        self.setAutoDelete(False)

    def emit_progress(self, event: dict):
        self.signals.progress.emit(event)

    def cancel(self):
        self.token.cancel()

    def run(self):
        current_cancel.set(self.token)
        log.info("job start: %s", self.name)
        try:
            res = self.fn(*self.args, **self.kwargs)
        except Cancelled:
            log.info("job cancelled: %s", self.name)
            self.signals.cancelled.emit()
            return
        except Exception as e:
            log.exception("job failed: %s", self.name)
            self.signals.failed.emit(f"{type(e).__name__}: {e}", traceback.format_exc())
            return
        finally:
            current_cancel.set(None)
        log.info("job done: %s", self.name)
        self.signals.finished.emit(res)


class JobManager(QObject):
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(4)
        self.active: list[Job] = []

    def start(self, job: Job) -> Job:
        self.active.append(job)
        for sig in (job.signals.finished, job.signals.failed, job.signals.cancelled):
            sig.connect(lambda *a, j=job: self._done(j))
        self.pool.start(job)
        self.busy_changed.emit(True)
        return job

    def _done(self, job: Job):
        if job in self.active:
            self.active.remove(job)
        self.busy_changed.emit(bool(self.active))

    def cancel_all(self):
        for j in list(self.active):
            j.cancel()
