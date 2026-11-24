"""Settings: AI provider/model/key, KiCad, viewer and engineering defaults."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTabWidget, QVBoxLayout,
                               QWidget)

from ..ai.providers.base import ProviderConfig, ProviderError
from ..ai.providers.scripted import scripted_allowed
from ..ai.providers.vendors import DEFAULT_MODELS, make_provider
from ..security import secrets

PROVIDERS = [("openai", "OpenAI"), ("gemini", "Google Gemini"), ("openai_compatible", "OpenAI-compatible (local server)")]


class SettingsDialog(QDialog):
    def __init__(self, settings, caps, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(640, 520)
        self.s = settings
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # ---- AI
        ai = QWidget()
        f = QFormLayout(ai)
        self.provider = QComboBox()
        provs = PROVIDERS + ([("scripted", "Simulated AI (development only)")] if scripted_allowed() else [])
        for k, label in provs:
            self.provider.addItem(label, k)
        i = self.provider.findData(settings.ai.provider)
        self.provider.setCurrentIndex(max(0, i))
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setCurrentText(settings.ai.model)
        self.list_btn = QPushButton("List models")
        mrow = QHBoxLayout()
        mrow.addWidget(self.model, 1)
        mrow.addWidget(self.list_btn)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("stored in the OS keychain - leave empty to keep the current key")
        self.base_url = QLineEdit(settings.ai.base_url)
        self.base_url.setPlaceholderText("e.g. http://localhost:11434/v1 (OpenAI-compatible only)")
        self.temp = QDoubleSpinBox()
        self.temp.setRange(0, 1.5)
        self.temp.setSingleStep(0.1)
        self.temp.setValue(settings.ai.temperature)
        self.explain = QCheckBox("Ask the AI to explain generation results")
        self.explain.setChecked(settings.ai.explain_results)
        self.key_status = QLabel(secrets.backend_status()[1])
        self.key_status.setObjectName("muted")
        self.key_status.setWordWrap(True)
        self.test_btn = QPushButton("Test connection")
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        f.addRow("Provider", self.provider)
        f.addRow("Model", mrow)
        f.addRow("API key", self.key)
        f.addRow("", self.key_status)
        f.addRow("Base URL", self.base_url)
        f.addRow("Temperature", self.temp)
        f.addRow("", self.explain)
        f.addRow(self.test_btn, self.test_result)
        tabs.addTab(ai, "AI")
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.list_btn.clicked.connect(self._list_models)
        self.test_btn.clicked.connect(self._test)

        # ---- KiCad
        kc = QWidget()
        kf = QFormLayout(kc)
        self.cli = QLineEdit(settings.kicad.cli_path)
        self.cli.setPlaceholderText(caps.cli_path or "auto-detect")
        self.jar = QLineEdit(settings.kicad.freerouting_jar)
        self.jar.setPlaceholderText("bundled tools/freerouting-1.9.0.jar")
        self.profile = QComboBox()
        self.profile.addItem("Standard (route + optimise)", "standard")
        self.profile.addItem("Fast (no optimisation)", "fast")
        self.profile.setCurrentIndex(max(0, self.profile.findData(settings.kicad.routing_profile)))
        info = QLabel(f"Detected: KiCad {caps.version or 'not found'}<br>CLI: {caps.cli_path or '-'}<br>"
                      f"Python bindings: {caps.python_path or 'not found'}<br>"
                      f"DRC: {'CLI' if caps.cli_drc else 'API' if caps.api_drc else 'no'} · ERC: {'CLI' if caps.cli_erc else 'no'}"
                      f" · IPC API: {'available (KiCad 9)' if caps.ipc_api else 'no'}<br>" +
                      "<br>".join(caps.notes))
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setWordWrap(True)
        info.setObjectName("muted")
        kf.addRow("kicad-cli path", self.cli)
        kf.addRow("Freerouting jar", self.jar)
        kf.addRow("Routing profile", self.profile)
        kf.addRow("", info)
        tabs.addTab(kc, "KiCad")

        # ---- Viewer / UI
        uv = QWidget()
        uf = QFormLayout(uv)
        self.soft3d = QCheckBox("Software 3D rendering (for virtual machines / remote desktops; restart required)")
        self.soft3d.setChecked(settings.ui.software_3d)
        self.logpanel = QCheckBox("Show engineering log panel")
        self.logpanel.setChecked(settings.ui.show_log_panel)
        self.reduce_motion = QCheckBox("Reduce motion (no animations)")
        self.reduce_motion.setChecked(getattr(settings.ui, "reduce_motion", False))
        uf.addRow("", self.soft3d)
        uf.addRow("", self.logpanel)
        uf.addRow("", self.reduce_motion)
        tabs.addTab(uv, "Viewer")

        # ---- Engineering
        ev = QWidget()
        ef = QFormLayout(ev)
        g = QLabel("Engineering rules are applied by the deterministic compiler: 0.25 mm signal tracks, 0.2 mm "
                   "clearance, 0.8/0.4 mm vias, 0.2 mm minimum drill, 0.5 mm edge clearance. Per-design rules "
                   "come from the requirements (layers, size, holes) and are shown in every proposal.")
        g.setWordWrap(True)
        g.setObjectName("muted")
        self.units = QComboBox()
        self.units.addItem("Millimetres", "mm")
        ef.addRow("Units", self.units)
        ef.addRow("", g)
        tabs.addTab(ev, "Engineering")

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._provider_changed()

    def _provider_changed(self):
        k = self.provider.currentData()
        self.base_url.setEnabled(k == "openai_compatible")
        self.key.setEnabled(k != "scripted")
        if self.model.currentText() in DEFAULT_MODELS.values() or not self.model.currentText():
            self.model.setCurrentText(DEFAULT_MODELS.get(k, "simulated"))

    def _cfg(self) -> ProviderConfig:
        k = self.provider.currentData()
        key = self.key.text().strip() or secrets.get_key(k)
        return ProviderConfig(k, self.model.currentText().strip(), key, self.base_url.text().strip() or None,
                              self.temp.value(), 20)

    def _list_models(self):
        try:
            models = make_provider(self._cfg()).list_models()
            cur = self.model.currentText()
            self.model.clear()
            self.model.addItems(models)
            self.model.setCurrentText(cur)
            self.test_result.setText(f"{len(models)} models available.")
        except ProviderError as e:
            self.test_result.setText(f"<span style='color:#F59E0B'>{e.kind}: {e}</span>")

    def _test(self):
        try:
            from ..ai.providers.base import Message
            out = make_provider(self._cfg()).complete_text("Reply with the single word OK.", [Message("user", "ping")])
            self.test_result.setText(f"<span style='color:#22C55E'>Connected. Model replied: {out[:40]}</span>")
        except ProviderError as e:
            self.test_result.setText(f"<span style='color:#F59E0B'>{e.kind}: {str(e)[:200]}</span>")

    def _save(self):
        s = self.s
        s.ai.provider = self.provider.currentData()
        s.ai.model = self.model.currentText().strip()
        s.ai.base_url = self.base_url.text().strip()
        s.ai.temperature = self.temp.value()
        s.ai.explain_results = self.explain.isChecked()
        if self.key.text().strip():
            secrets.set_key(s.ai.provider, self.key.text().strip())
        s.kicad.cli_path = self.cli.text().strip()
        s.kicad.freerouting_jar = self.jar.text().strip()
        s.kicad.routing_profile = self.profile.currentData()
        s.ui.software_3d = self.soft3d.isChecked()
        s.ui.show_log_panel = self.logpanel.isChecked()
        s.ui.reduce_motion = self.reduce_motion.isChecked()
        from . import motion
        motion.set_enabled(not s.ui.reduce_motion)
        s.save()
        self.accept()
