"""Embedded web viewers (2D PCB, 3D PCB, schematic sheets) on the loopback viewer server."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from ..core.localserver import ViewerServer

log = logging.getLogger(__name__)


class BridgePage(QWebEnginePage):
    message = Signal(dict)

    def javaScriptConsoleMessage(self, level, text, line, source):
        if text.startswith("AIPCB:"):
            try:
                self.message.emit(json.loads(text[6:]))
            except ValueError:
                pass
        elif level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            log.warning("viewer js error: %s (%s:%s)", text, source, line)


class WebViewer(QWebEngineView):
    message = Signal(dict)

    def __init__(self, server: ViewerServer, parent=None):
        super().__init__(parent)
        self.server = server
        page = BridgePage(self)
        page.message.connect(self.message)
        self.setPage(page)
        s = self.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
        self.page().setBackgroundColor(Qt_color("#0B1120"))

    def js(self, code: str):
        self.page().runJavaScript(code)


def Qt_color(hexs):
    from PySide6.QtGui import QColor
    return QColor(hexs)


class Pcb2DViewer(WebViewer):
    component_selected = Signal(str)

    def __init__(self, server, parent=None):
        super().__init__(server, parent)
        self.message.connect(self._on_msg)
        self.show_revision(None)

    def show_revision(self, rev_dir: Path | None):
        has = rev_dir is not None and (rev_dir / "renders" / "layers" / "board.json").exists()
        base = quote(self.server.rev_url("renders/layers/"), safe="") if has else ""
        self.setUrl(QUrl(self.server.app_url(f"svg/viewer2d.html?base={base}")))

    def highlight(self, ref: str):
        self.js(f"window.highlightRef && window.highlightRef({json.dumps(ref)})")

    def _on_msg(self, m: dict):
        if "select" in m:
            self.component_selected.emit(m["select"] or "")


class Pcb3DViewer(WebViewer):
    def show_revision(self, rev_dir: Path | None, missing: list[str] | None = None):
        glb = next(iter(sorted((rev_dir / "3d").glob("*.glb"))), None) if rev_dir and (rev_dir / "3d").exists() else None
        if glb is None:
            self.setUrl(QUrl(self.server.app_url("threejs/viewer.html")))
            return
        model = quote(self.server.rev_url(f"3d/{glb.name}"), safe="")
        miss = quote(",".join(missing or []), safe="")
        self.setUrl(QUrl(self.server.app_url(f"threejs/viewer.html?model={model}&missing={miss}")))


class SchematicViewer(WebViewer):
    def show_revision(self, rev_dir: Path | None, circuit_name: str = ""):
        pages = []
        if rev_dir and (rev_dir / "renders").exists():
            svgs = sorted((rev_dir / "renders").glob(f"{circuit_name}*.svg")) if circuit_name else []
            svgs = [s for s in svgs if not s.name.startswith("pcb_")]
            root = [s for s in svgs if s.stem == circuit_name]
            for s in root + [s for s in svgs if s.stem != circuit_name]:
                label = "Root" if s.stem == circuit_name else s.stem[len(circuit_name) + 1:]
                pages.append({"name": label, "url": self.server.rev_url(f"renders/{s.name}")})
        self.setUrl(QUrl(self.server.app_url("svg/sheets.html?pages=" + quote(json.dumps(pages), safe=""))))
