"""Main window: sidebar, home screen and the project workspace."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QSplitter, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from ..ai.orchestrator import Stage
from ..ai.providers.base import ProviderConfig, ProviderError
from ..ai.providers.vendors import make_provider
from ..core.config import data_dir, freerouting_jar, package_root
from ..core.localserver import ViewerServer
from ..core.logging_setup import RING
from ..core.service import ProjectSession
from ..engineering.design.spec import DesignSpec
from ..engineering.pipeline import DesignPipeline
from ..kicad.detection import KiCadCapabilities, detect
from ..kicad.library import LibraryManager
from ..security import secrets
from ..storage.projects import ProjectManager
from .chat import STATUS_LABEL, ChatPanel
from .home import HomeScreen
from .jobs import Job, JobManager
from .panels import InspectorPanel, LogPanel, SummaryView, ValidationView
from .settings_dialog import SettingsDialog
from .viewers import Pcb2DViewer, Pcb3DViewer, SchematicViewer
from . import icons
from .icons import app_icon
from .theme import Backdrop, TEXT3
from .components import HeaderMark, IconButton, Segmented, button, hair, label

log = logging.getLogger(__name__)

EXAMPLE_SPECS = {
    "fan": dict(project_name="Smart fan controller", controller="esp32", input_voltage=12,
                loads={"count": 1, "kind": "dc_fan", "voltage": 12, "current_per_channel_a": 0.5},
                sensors=[{"kind": "ds18b20"}], mounting_holes=2),
    "ext8": dict(project_name="8-channel I2C load driver", controller="external_i2c", input_voltage=24,
                 loads={"count": 8, "kind": "led_strip", "voltage": 24, "current_per_channel_a": 0.3},
                 sensors=[{"kind": "sht31"}]),
    "esp24": dict(project_name="24-channel Wi-Fi controller", controller="esp32", input_voltage=12,
                  loads={"count": 24, "kind": "solenoid", "voltage": 12, "current_per_channel_a": 0.5}),
}
CHAT_EXAMPLES = [
    "I want an ESP32 board that controls a 12 V fan based on temperature, with two mounting holes.",
    "An 8-channel PWM LED strip driver at 24 V, 0.3 A per channel, controlled over I2C by a Raspberry Pi.",
    "ESP32 controller for 16 solenoid valves at 12 V, 0.5 A each, plus a humidity sensor.",
]


class MainWindow(QMainWindow):
    def __init__(self, settings, branding):
        super().__init__()
        self.settings, self.branding = settings, branding
        self.setWindowTitle(branding.name)
        self.setWindowIcon(app_icon())
        self.resize(1600, 960)
        self.setMinimumSize(1180, 720)
        self.jobs = JobManager()
        self.pm = ProjectManager(data_dir())
        self.server = ViewerServer(package_root() / "visualization")
        self.caps: KiCadCapabilities | None = None
        self.libs: LibraryManager | None = None
        self.session: ProjectSession | None = None
        self.gen_job: Job | None = None

        root = Backdrop(branding)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._header())
        from .motion import FadeStack
        self.stack = FadeStack()
        self.stack.setObjectName("transparent")
        v.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.home = HomeScreen(branding)
        self.home.new_project.connect(self.new_project)
        self.home.start_with_prompt.connect(self.start_from_prompt)
        self.home.open_project.connect(self.open_project)
        self.home.open_example.connect(self.open_example)
        self.home.open_settings.connect(self.open_settings)
        self.stack.addWidget(self.home)
        self.stack.addWidget(self._workspace())
        self.status = self.statusBar()
        self.status.setSizeGripEnabled(False)
        self.status_label = QLabel("Detecting KiCad…")
        self.status.addWidget(self.status_label, 1)
        self.status_right = QLabel("")
        self.status.addPermanentWidget(self.status_right)
        self.refresh_home()
        self._nav("home")
        self._detect()

    # =================================================================== layout
    def _header(self) -> QWidget:
        hd = QWidget()
        hd.setObjectName("header")
        hd.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        hd.setFixedHeight(56)
        h = QHBoxLayout(hd)
        h.setContentsMargins(18, 0, 12, 0)
        h.setSpacing(0)
        mark = HeaderMark(self.branding)
        mark.setCursor(Qt.CursorShape.PointingHandCursor)
        mark.setToolTip("Home")
        mark.mousePressEvent = lambda e: self.go_home()
        h.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addSpacing(22)
        self.crumb_sep = hair(vertical=True)
        self.crumb_sep.setFixedHeight(22)
        h.addWidget(self.crumb_sep, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addSpacing(18)
        self.crumb_home = button("Boards", "text")
        self.crumb_home.setStyleSheet("color:#6B778D; font-size:14px; padding:4px 2px;")
        self.crumb_home.clicked.connect(self.go_home)
        h.addWidget(self.crumb_home, 0, Qt.AlignmentFlag.AlignVCenter)
        self.crumb_slash = label("/", "crumbDim")
        self.crumb_slash.setContentsMargins(8, 0, 8, 0)
        h.addWidget(self.crumb_slash, 0, Qt.AlignmentFlag.AlignVCenter)
        self.proj_title = label("", "crumb")
        h.addWidget(self.proj_title, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addSpacing(10)
        self.rev_label = label("", "chip")
        h.addWidget(self.rev_label, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addStretch(1)
        from .motion import PulseDot
        self.stage_dot = PulseDot()
        self.stage_dot.hide()
        h.addWidget(self.stage_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.proj_badge = label("", "dim")
        h.addWidget(self.proj_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addSpacing(14)
        self.nav_btns: dict[str, IconButton] = {}
        for key, ic, tip, fn in (("new", "plus", "New board", self.new_project),
                                 ("chat", "panel-left", "Show or hide the conversation", self.toggle_chat),
                                 ("log", "terminal", "Show or hide the engineering log", self.toggle_log),
                                 ("settings", "settings", "Settings", self.open_settings)):
            b = IconButton(ic, tip)
            b.clicked.connect(fn)
            h.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
            self.nav_btns[key] = b
        more = IconButton("ellipsis", "More")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(more)
        m.addAction(icons.icon("external-link", "#A3AEC2"), "Open in KiCad", self.open_in_kicad)
        m.addAction(icons.icon("folder", "#A3AEC2"), "Open data folder",
                    lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir()))))
        m.addSeparator()
        m.addAction(icons.icon("circle-help", "#A3AEC2"), "Help", self.show_help)
        m.addAction(icons.icon("file-archive", "#A3AEC2"), "Save diagnostic report...", self.save_diagnostics)
        m.addAction(icons.icon("info", "#A3AEC2"), "About", self.show_about)
        more.setMenu(m)
        h.addWidget(more, 0, Qt.AlignmentFlag.AlignVCenter)
        return hd

    def _set_crumbs(self, visible: bool):
        for w in (self.crumb_sep, self.crumb_home, self.crumb_slash, self.proj_title, self.rev_label, self.proj_badge):
            w.setVisible(visible)
        for k in ("chat", "log"):
            self.nav_btns[k].setVisible(visible)
        if not visible:
            self.stage_dot.hide()

    def _workspace(self) -> QWidget:
        w = QWidget()
        w.no_fade = True
        w.setObjectName("transparent")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.vsplit = QSplitter(Qt.Orientation.Vertical)
        self.hsplit = QSplitter(Qt.Orientation.Horizontal)
        self.hsplit.setHandleWidth(1)
        self.vsplit.setHandleWidth(1)
        self.chat = ChatPanel()
        self.chat.send_message.connect(self.on_send)
        self.chat.approve.connect(self.on_approve)
        self.chat.reject.connect(self.on_reject)
        self.chat.cancel_job.connect(self.on_cancel)
        self.chat.open_tab.connect(self.open_tab)
        self.chat.close_requested.connect(self.toggle_chat)

        canvas = QWidget()
        canvas.setObjectName("canvas")
        canvas.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cl = QVBoxLayout(canvas)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        bar = QWidget()
        bar.setObjectName("transparent")
        bar.setFixedHeight(52)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 0, 14, 0)
        self.seg = Segmented([("board", "Board"), ("2d", "2D"), ("3d", "3D"), ("sch", "Schematic"),
                              ("sum", "Summary"), ("val", "Checks")])
        self.seg.changed.connect(self._show_view)
        bl.addWidget(self.seg, 0, Qt.AlignmentFlag.AlignVCenter)
        bl.addStretch(1)
        self.view_hint = label("", "dim")
        bl.addWidget(self.view_hint)
        cl.addWidget(bar)
        cl.addWidget(hair())
        self.views = QStackedWidget()
        self.v2d = Pcb2DViewer(self.server)
        self.v3d = Pcb3DViewer(self.server)
        self.vsch = SchematicViewer(self.server)
        self.summary = SummaryView()
        self.validation = ValidationView()
        self.board_split = QSplitter(Qt.Orientation.Horizontal)
        self.board_split.setHandleWidth(1)
        self.board_split.addWidget(self.v2d)
        self.board_split.addWidget(self.v3d)
        self.board_split.setChildrenCollapsible(True)
        self.views.addWidget(self.board_split)
        self.views.addWidget(self.vsch)
        self.views.addWidget(self.summary)
        self.views.addWidget(self.validation)
        cl.addWidget(self.views, 1)

        self.inspector = InspectorPanel()
        self.inspector.setMinimumWidth(290)
        self.inspector.rollback.connect(self.on_rollback)
        self.inspector.export.connect(self.on_export)
        self.inspector.open_in_kicad.connect(self.open_in_kicad)
        self.v2d.component_selected.connect(self.inspector.show_component)
        self.chat.setMinimumWidth(340)
        self.hsplit.addWidget(self.chat)
        self.hsplit.addWidget(canvas)
        self.hsplit.addWidget(self.inspector)
        self.hsplit.setStretchFactor(1, 1)
        self.hsplit.setSizes([max(400, self.settings.ui.chat_width), 860, 310])
        self.logpanel = LogPanel()
        self.logpanel.attach(RING)
        self.logpanel.open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir() / "logs"))))
        self.vsplit.addWidget(self.hsplit)
        self.vsplit.addWidget(self.logpanel)
        self.vsplit.setSizes([800, 170])
        self.logpanel.setVisible(self.settings.ui.show_log_panel)
        outer.addWidget(self.vsplit, 1)
        self.seg.select("board")
        return w

    def _show_view(self, key: str):
        hints = {"board": "2D layout and 3D render side by side", "2d": "Click a part to inspect it",
                 "3d": "Drag to orbit, scroll to zoom", "sch": "", "sum": "", "val": ""}
        self.view_hint.setText(hints.get(key, ""))
        if key in ("board", "2d", "3d"):
            self.views.setCurrentIndex(0)
            total = max(2, self.board_split.width())
            self.board_split.setSizes({"board": [total // 2, total - total // 2], "2d": [total, 0],
                                       "3d": [0, total]}[key])
        else:
            self.views.setCurrentIndex({"sch": 1, "sum": 2, "val": 3}[key])

    # ============================================================ navigation
    def _nav(self, key):
        self._set_crumbs(key == "work")

    def go_home(self):
        self.refresh_home()
        self.setWindowTitle(self.branding.name)
        self.stack.setCurrentIndex(0)
        self._nav("home")

    def go_work(self):
        if self.session is None:
            self.go_home()
            return
        self.stack.setCurrentIndex(1)
        self._nav("work")

    def toggle_chat(self):
        self.chat.setVisible(not self.chat.isVisible())

    def toggle_log(self):
        self.logpanel.setVisible(not self.logpanel.isVisible())

    def open_tab(self, name: str):
        key = {"2D PCB": "2d", "3D PCB": "3d", "Schematic": "sch", "Design Summary": "sum", "Summary": "sum",
               "Validation": "val", "Board": "board"}.get(name, "board")
        if self.session is not None and self.stack.currentIndex() != 1:
            self.go_work()
        self.seg.select(key)

    def show_help(self):
        QMessageBox.information(self, f"{self.branding.name} help", (
            "1. Start a new design and describe the board in the chat.\n"
            "2. Answer the engineer's questions; critical electrical values are never guessed.\n"
            "3. Review the proposal (parts, rules, assumptions, risks) and approve it.\n"
            "4. The app generates a real KiCad schematic and PCB, routes it, runs KiCad DRC/ERC and repairs what it "
            "safely can. Results are shown exactly as KiCad reports them.\n"
            "5. Ask for changes in the chat to create a new revision; roll back any time.\n"
            "6. Export the fabrication package, BOM, pick & place, STEP, or the native KiCad project.\n\n"
            f"Data folder: {data_dir()}"))

    def show_about(self):
        from .about import AboutDialog
        AboutDialog(self.branding, self).exec()

    def save_diagnostics(self):
        """Zip of logs + system facts + the current revision's run record, for a GitHub issue."""
        from ..core import diagnostics
        from ..core.version import links
        import time as _t
        dest, _ = QFileDialog.getSaveFileName(self, "Save diagnostic report",
                                              f"gerber_bear_diagnostics_{_t.strftime('%Y%m%d_%H%M')}.zip", "Zip (*.zip)")
        if not dest:
            return
        rev = self.session.current_revision() if self.session else None
        try:
            diagnostics.build(Path(dest), self.caps, rev.dir if rev else None)
        except Exception as e:
            QMessageBox.warning(self, "Diagnostic report", f"Could not write the report: {e}")
            return
        url = links().get("new_issue")
        msg = (f"Saved {Path(dest).name}. It contains the app log, system details and the current revision's run "
               "record - no API keys.")
        if url and QMessageBox.question(self, "Diagnostic report", msg + "\n\nOpen GitHub to report the problem "
                                        "(attach this file)?") == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl(url))
        elif not url:
            QMessageBox.information(self, "Diagnostic report", msg)

    def _check_updates(self):
        """Once per start, in the background: is a newer release on GitHub?"""
        from ..core.version import latest_release
        job = Job("update check", latest_release)
        job.signals.finished.connect(self._update_found)
        job.signals.failed.connect(lambda *a: None)
        self.jobs.start(job)

    def _update_found(self, res):
        if res:
            self.status_label.setText(f"Version {res['version']} is available: {res['url']}")

    # ============================================================ integration
    def _detect(self):
        def work():
            caps = detect(self.settings.kicad.cli_path or None)
            libs = LibraryManager(caps.symbol_dir, caps.footprint_dir, caps.models3d_dir) if caps.symbol_dir else None
            return caps, libs
        job = Job("detect KiCad", work)
        job.signals.finished.connect(self._detected)
        job.signals.failed.connect(lambda m, t: self.status_label.setText(f"KiCad detection failed: {m}"))
        self.jobs.start(job)

    def _detected(self, res):
        self.caps, self.libs = res
        c = self.caps
        self.status_label.setText(f"KiCad {c.version} ready" if c.installed else "KiCad not found")
        self._kicad_text = f"KiCad {c.version}" if c.installed else "KiCad not found"
        self._update_status_right()
        self.refresh_home()
        if self.session:
            self.session.libs = self.libs
            self.session.pipeline = self._pipeline()
            self.session.set_provider(self._provider()[0])
        self._maybe_welcome()
        if not getattr(self, "_update_checked", False):
            self._update_checked = True
            self._check_updates()

    def _maybe_welcome(self):
        """First run (or anything still missing): show what to set up, once per launch."""
        if getattr(self, "_welcomed", False) or os.environ.get("AIPCB_NO_WELCOME") == "1":
            return
        self._welcomed = True
        a = self.settings.ai
        has_key = a.provider in ("openai_compatible", "scripted") or bool(secrets.get_key(a.provider))
        kicad_ok = bool(self.caps and self.caps.installed and (self.caps.major or 0) >= 8)
        if has_key and kicad_ok:
            return
        from .welcome import WelcomeDialog
        QTimer.singleShot(300, lambda: WelcomeDialog(self.caps, has_key, self.open_settings, self).exec())

    def _pipeline(self):
        if not self.caps or not self.caps.installed or not self.caps.python_path:
            return None
        return DesignPipeline(self.caps, freerouting_jar(self.settings), self.settings.kicad.routing_profile)

    def _provider(self):
        a = self.settings.ai
        key = secrets.get_key(a.provider)
        if a.provider in ("openai", "gemini") and not key:
            return None, f"{a.provider}: no API key. Open Settings > AI."
        try:
            p = make_provider(ProviderConfig(a.provider, a.model, key, a.base_url or None, a.temperature))
        except ProviderError as e:
            return None, str(e)
        label = {"openai": "OpenAI", "gemini": "Gemini", "openai_compatible": "Local", "scripted": "Simulated"}[a.provider]
        return p, f"{label} · {a.model}"

    def _update_status_right(self):
        self.status_right.setText("   ·   ".join(t for t in (getattr(self, "_kicad_text", ""),
                                                             getattr(self, "_ai_text", "")) if t))

    def _thumb_for(self, pid: str):
        try:
            cur = self.pm.get(pid).current_revision
            if cur:
                f = self.pm.revision(pid, cur).dir / "renders" / "layers"
                return f if (f / "layer_Edge_Cuts.svg").exists() else None
        except Exception:
            return None
        return None

    def refresh_home(self):
        self.home.set_projects(self.pm.list(), self._thumb_for)
        p, text = self._provider()
        self._ai_text = text if p else "AI not set up"
        self._update_status_right()
        if self.caps:
            self.home.set_status(self.caps, text + (" (simulated, development only)" if p and p.simulated else ""),
                                 p is not None, freerouting_jar(self.settings).exists())

    # ================================================================ projects
    def new_project(self):
        name, ok = QInputDialog.getText(self, "New PCB design", "Project name:", text="New PCB design")
        if not ok:
            return
        p = self.pm.create(name)
        self.open_project(p.id)

    def start_from_prompt(self, text: str):
        import re
        words = re.findall(r"[A-Za-z0-9][\w\-]*", text)
        stop = {"a", "an", "the", "i", "want", "need", "board", "that", "with", "for", "and", "to", "of", "at", "my"}
        name = " ".join(w for w in words if w.lower() not in stop)[:40].strip().capitalize() or "New board"
        p = self.pm.create(name)
        self.open_project(p.id, welcome=False)
        self.on_send(text)

    def open_example(self, key: str):
        if not self.libs:
            QMessageBox.warning(self, "KiCad unavailable", "KiCad libraries are required to compile designs.")
            return
        spec = DesignSpec.model_validate(EXAMPLE_SPECS[key])
        p = self.pm.create(spec.project_name)
        self.open_project(p.id)
        self.chat.set_busy(True, "Compiling the example design…")
        job = Job("compile example", self.session.load_spec, spec)
        job.signals.finished.connect(lambda r: (self.chat.set_busy(False), self._show_items(r["items"], interactive=True),
                                                self._refresh_project()))
        job.signals.failed.connect(lambda m, t: (self.chat.set_busy(False), self._error(m)))
        self.jobs.start(job)

    def open_project(self, pid: str, welcome: bool = True):
        try:
            prov, text = self._provider()
            self.session = ProjectSession(self.pm, pid, prov, self._pipeline(), self.libs)
        except (KeyError, FileNotFoundError) as e:
            QMessageBox.warning(self, "Cannot open project", str(e))
            return
        self.chat.set_provider(text if prov else "", bool(prov and prov.simulated), prov is not None)
        self.chat.clear()
        self.chat.set_busy(False)
        self.chat.setVisible(True)
        hist = self.session.history()
        if not hist and welcome:
            self.chat.add_welcome(CHAT_EXAMPLES)
        last_prop = max((i for i, m in enumerate(hist) if m["kind"] == "proposal"), default=-1)
        for i, m in enumerate(hist):
            self.chat.add_item(m["role"], m["kind"], m["text"], m["data"],
                               interactive=(i == last_prop and self.session.state.stage == Stage.proposal))
        self._refresh_project()
        self.seg.select("board")
        self.go_work()

    def _refresh_project(self):
        s = self.session
        if not s:
            return
        p = self.pm.get(s.pid)
        self.proj_title.setText(p.name)
        self.setWindowTitle(f"{p.name} — {self.branding.name}")
        stage = s.state.stage.value
        self.proj_badge.setText({"gathering": "Gathering requirements", "proposal": "Waiting for your approval",
                                 "generating": "Building with KiCad", "generated": ""}.get(stage, stage))
        from .theme import GOLD as _G, TEAL_SOFT as _T
        self.stage_dot.setVisible(stage in ("proposal", "generating", "gathering"))
        self.stage_dot.set(_G if stage == "proposal" else _T, stage == "generating")
        rev = s.current_revision()
        revs = self.pm.revisions(s.pid)

        self.server.set_revision(rev.dir if rev else None)
        exports = (rev.summary or {}) if rev else {}
        missing = []
        if rev and (rev.dir / "run.json").exists():
            import json
            for st in json.loads((rev.dir / "run.json").read_text()).get("steps", []):
                if st["name"] == "exports":
                    missing = st["detail"].get("components_without_3d_model", [])
        _ = exports
        self.v2d.show_revision(rev.dir if rev else None)
        self.v3d.show_revision(rev.dir if rev else None, missing)
        self.vsch.show_revision(rev.dir if rev else None, rev.circuit_name if rev else "")
        self.summary.show_data(s.state.spec.model_dump(), s.state.proposal)
        self.validation.show_revision(rev)
        self.inspector.show_project(rev, revs, p.current_revision)
        self.rev_label.setText(f"r{rev.rev}" if rev else "draft")

    def _show_items(self, items, interactive=False):
        for it in items:
            self.chat.add_item(it.role, it.kind, it.text, it.data, interactive=interactive and it.kind == "proposal")

    def _error(self, msg):
        self.chat.add_item("system", "error", f"Something went wrong: {msg}\n\nDetails are in the engineering log.")

    # =================================================================== chat
    def on_send(self, text: str):
        if not self.session:
            return
        self.chat.add_item("user", "text", text)
        self.chat.set_busy(True)
        self.chat.disable_proposal_buttons()
        sess = self.session
        job = Job("ai turn", sess.user_message, text)
        # The reply belongs to the board it was asked on: if the user has opened another board
        # meanwhile, it is saved there (by the session) but not shown in the current chat.
        job.signals.finished.connect(lambda r: self._turn_done(r) if self.session is sess else None)
        job.signals.failed.connect(lambda m, t: (self.chat.set_busy(False), self._error(m))
                                   if self.session is sess else None)
        self.jobs.start(job)

    def _turn_done(self, res):
        self.chat.set_busy(False)
        self._show_items(res.items, interactive=True)
        self._refresh_project()
        if res.action == "generate":
            self.start_generation()

    def on_approve(self):
        if not self.session:
            return
        self.chat.disable_proposal_buttons()
        res = self.session.approve()
        if res.action == "generate" or (self.session.orch is None and self.session.state.stage == Stage.proposal):
            self.start_generation()

    def on_reject(self):
        if self.session:
            self.chat.disable_proposal_buttons()
            self._show_items(self.session.reject().items)
            self._refresh_project()

    def start_generation(self):
        s = self.session
        if s.pipeline is None:
            self._error("KiCad integration is unavailable on this machine (see the home screen status).")
            return
        self.chat.add_item("assistant", "text", "Generating the schematic and PCB with KiCad…")
        self.chat.start_progress()
        self.chat.set_busy(True, "Generation running…")
        job = Job("generate", lambda: s.generate(on_event=job.emit_progress))
        self.gen_job = job
        job.signals.progress.connect(self._gen_progress)
        job.signals.finished.connect(self._gen_done)
        job.signals.failed.connect(self._gen_failed)
        job.signals.cancelled.connect(lambda: self._gen_failed("cancelled", ""))
        self.jobs.start(job)
        self._refresh_project()

    def _gen_progress(self, e):
        self.chat.progress_event(e)
        if e["type"] == "step_start":
            self.status_label.setText(f"Generating: {e['name']}…")

    def _gen_done(self, out):
        self.chat.end_progress()
        self.chat.set_busy(False)
        self.status_label.setText(f"Revision r{out['revision']}: {out['status']}")
        self._show_items(out["items"])
        self._refresh_project()
        self.open_tab("Board")
        self.gen_job = None

    def _gen_failed(self, m, t=""):
        self.chat.end_progress()
        self.chat.set_busy(False)
        self._error(m)
        self._refresh_project()
        self.gen_job = None

    def on_cancel(self):
        if self.gen_job:
            self.gen_job.cancel()
            self.status_label.setText("Cancelling…")

    def on_rollback(self, rev: int):
        if self.session:
            it = self.session.rollback(rev)
            self.chat.add_item(it.role, it.kind, it.text)
            self._refresh_project()

    # ================================================================ outputs
    def on_export(self, kind: str):
        s = self.session
        rev = s.current_revision() if s else None
        if not rev:
            return
        name = rev.circuit_name or "design"
        try:
            if kind == "native":
                dest, _ = QFileDialog.getSaveFileName(self, "Export KiCad project", f"{name}_r{rev.rev}_kicad.zip", "Zip (*.zip)")
                if dest:
                    self.pm.export_native(s.pid, rev.rev, Path(dest))
            elif kind == "fab":
                dest = ""
                if (rev.dir / "fab" / "order").is_dir():
                    folder = QFileDialog.getExistingDirectory(self, "Choose where to save the order package")
                    if folder:
                        out = self.pm.export_order(s.pid, rev.rev, Path(folder))
                        if out:
                            dest = str(out)
                            QDesktopServices.openUrl(QUrl.fromLocalFile(dest))
                else:                                   # older revision: plain zip of the fab folder
                    dest, _ = QFileDialog.getSaveFileName(self, "Export fabrication files", f"{name}_r{rev.rev}_fab.zip", "Zip (*.zip)")
                    if dest:
                        self.pm.export_fab(s.pid, rev.rev, Path(dest))
            elif kind in ("bom", "pos"):
                src = next(iter((rev.dir / "fab" / "order").glob("*_BOM.csv")), None) if kind == "bom" else None
                src = src or next(iter((rev.dir / "fab").glob(f"*-{kind}.csv")), None)
                if not src:
                    raise FileNotFoundError(f"no {kind.upper()} file in this revision")
                dest, _ = QFileDialog.getSaveFileName(self, "Export " + ("BOM" if kind == "bom" else "pick & place"),
                                                      f"{name}_r{rev.rev}_{kind}.csv", "CSV (*.csv)")
                if dest:
                    shutil.copyfile(src, dest)
            else:
                src = next(iter((rev.dir / ("3d" if kind == "step" else "renders")).glob("*.step" if kind == "step" else f"{name}.pdf")), None)
                if not src:
                    raise FileNotFoundError(f"no {kind.upper()} file in this revision")
                dest, _ = QFileDialog.getSaveFileName(self, f"Export {kind.upper()}", src.name)
                if dest:
                    shutil.copyfile(src, dest)
            if dest:
                self.status_label.setText(f"Exported {Path(dest).name}")
        except Exception as e:
            QMessageBox.warning(self, "Export failed", str(e))

    def open_in_kicad(self):
        s = self.session
        rev = s.current_revision() if s else None
        if not rev:
            QMessageBox.information(self, "Open in KiCad", "Open a board that has been built first: its KiCad "
                                    "project opens in KiCad.")
            return
        pro = rev.dir / f"{rev.circuit_name}.kicad_pro"
        name = "kicad.exe" if sys.platform == "win32" else "kicad"
        exe = shutil.which("kicad") or (str(Path(self.caps.cli_path).with_name(name))
                                        if self.caps and self.caps.cli_path else None)
        if not pro.exists() or not exe or not Path(exe).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(rev.dir)))
            return
        subprocess.Popen([exe, str(pro)], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # =============================================================== settings
    def open_settings(self):
        if not self.caps:
            return
        if SettingsDialog(self.settings, self.caps, self).exec():
            self.logpanel.setVisible(self.settings.ui.show_log_panel)
            if self.session:
                prov, text = self._provider()
                self.session.set_provider(prov)
                self.session.pipeline = self._pipeline()
                self.chat.set_provider(text if prov else "", bool(prov and prov.simulated), prov is not None)
            self.refresh_home()

    def closeEvent(self, ev):
        self.jobs.cancel_all()
        self.settings.ui.chat_width = self.hsplit.sizes()[0] or self.settings.ui.chat_width
        self.settings.save()
        self.server.stop()
        super().closeEvent(ev)
