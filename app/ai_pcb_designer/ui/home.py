"""Home: start a board by describing it; recent boards shown as their real renders."""
from __future__ import annotations

import time
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import icons
from .chat import STATUS_LABEL
from .components import Dot, HeroLockup, StatusLine, TONE_OF_STATUS, button, label
from .theme import INK, OK, TEXT2, TEXT3, WARN

PROMPTS = [
    ("Fan controller", "An ESP32 board that runs a 12 V fan from a temperature sensor, with two mounting holes."),
    ("LED strip driver", "An 8-channel PWM LED strip driver at 24 V, 0.3 A per channel, controlled over I2C."),
    ("Valve controller", "An ESP32 controller for 16 solenoid valves at 12 V, 0.5 A each, plus a humidity sensor."),
]
EXAMPLES = [("Smart fan controller", "fan"), ("8-channel I²C driver", "ext8"), ("24-channel Wi-Fi controller", "esp24")]
THUMB_W, THUMB_H = 312, 176


LAYER_STYLE = (("Edge_Cuts", None, 1.0), ("B_Cu", "#1BA3BA", 0.42), ("F_Cu", "#C9783A", 0.6),
               ("F_Silkscreen", "#E7ECF4", 0.85), ("Edge_Cuts", "#FFD700", 1.0))


def _layer_image(svg: Path, w: int, h: int) -> QImage:
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    r = QSvgRenderer(str(svg))
    if r.isValid():
        qp = QPainter(img)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        r.render(qp)
        qp.end()
    return img


def _alpha_bbox(img: QImage):
    import numpy as np
    a = np.frombuffer(img.constBits(), np.uint8, img.sizeInBytes()).reshape(img.height(), img.bytesPerLine() // 4, 4)[..., 3]
    ys, xs = np.nonzero(a > 10)
    if not len(xs):
        return None
    return xs.min(), ys.min(), xs.max(), ys.max()


@lru_cache(maxsize=64)
def _thumb(layers_dir: str, mtime: float) -> QPixmap:
    """Board thumbnail composed from KiCad's per-layer renders in the app's own colours."""
    d = Path(layers_dir)
    dpr = 2.0
    pm = QPixmap(int(THUMB_W * dpr), int(THUMB_H * dpr))
    pm.fill(QColor(INK))
    edge = d / "layer_Edge_Cuts.svg"
    if not edge.exists():
        return pm
    r = QSvgRenderer(str(edge))
    vb = r.viewBoxF()
    W = 1400
    H = max(1, int(W * vb.height() / max(vb.width(), 1)))
    edge_img = _layer_image(edge, W, H)
    bb = _alpha_bbox(edge_img)
    if bb is None:
        return pm
    x0, y0, x1, y1 = bb
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    k = min((THUMB_W - 40) * dpr / bw, (THUMB_H - 30) * dpr / bh)
    ox = (THUMB_W * dpr - bw * k) / 2 - x0 * k
    oy = (THUMB_H * dpr - bh * k) / 2 - y0 * k
    qp = QPainter(pm)
    qp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    qp.fillRect(QRectF(ox + x0 * k, oy + y0 * k, bw * k, bh * k), QColor("#16181D"))
    for name, color, alpha in LAYER_STYLE:
        f = d / f"layer_{name}.svg"
        if color is None or not f.exists():
            continue
        img = edge_img if name == "Edge_Cuts" else _layer_image(f, W, H)
        tint = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
        tint.fill(Qt.GlobalColor.transparent)
        tp = QPainter(tint)
        tp.drawImage(0, 0, img)
        tp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        tp.fillRect(tint.rect(), QColor(color))
        tp.end()
        qp.setOpacity(alpha)
        qp.drawImage(QRectF(ox, oy, W * k, H * k), tint)
    qp.end()
    pm.setDevicePixelRatio(dpr)
    return pm


class _ProjectCard(QFrame):
    clicked = Signal(str)

    def __init__(self, p: dict, svg: Path | None, parent=None):
        super().__init__(parent)
        self.pid = p["id"]
        self.setObjectName("card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(THUMB_W + 2)
        v = QVBoxLayout(self)
        v.setContentsMargins(1, 1, 1, 14)
        v.setSpacing(0)
        th = QLabel()
        th.setObjectName("thumb")
        th.setFixedSize(THUMB_W, THUMB_H)
        th.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if svg and (svg / "layer_Edge_Cuts.svg").exists():
            th.setPixmap(_thumb(str(svg), (svg / "layer_Edge_Cuts.svg").stat().st_mtime))
        else:
            th.setPixmap(icons.logo(54, "gerber_bear_mark_navy"))
            th.setStyleSheet(f"background:{INK}; border-top-left-radius:13px; border-top-right-radius:13px;")
        v.addWidget(th)
        v.addSpacing(12)
        t = label(p["name"], "title")
        t.setContentsMargins(16, 0, 16, 0)
        v.addWidget(t)
        v.addSpacing(4)
        meta = QHBoxLayout()
        meta.setContentsMargins(16, 0, 16, 0)
        meta.setSpacing(8)
        tone = TONE_OF_STATUS.get(p["status"] or "", "none")
        meta.addWidget(Dot({"ok": OK, "warn": WARN, "err": "#F87171"}.get(tone, TEXT3), 7), 0, Qt.AlignmentFlag.AlignVCenter)
        when = time.strftime("%d %b, %H:%M", time.localtime(p["modified"]))
        state = STATUS_LABEL.get(p["status"] or "", ("Draft", ""))[0].split(":")[0].split(",")[0]
        meta.addWidget(label(f"{state}  ·  {when}" + ("  ·  folder missing" if p["missing"] else ""), "dim"), 1)
        v.addLayout(meta)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.pid)


TEMPLATES = [
    ("fan", "Fan controller", "Wi-Fi control of 12 V fans with a temperature probe",
     "An ESP32 board that controls two 12 V PC fans at 0.3 A each by PWM, with a DS18B20 temperature probe. 12 V supply."),
    ("lightbulb", "LED strip driver", "8 dimmable 24 V strips, driven over I2C",
     "An 8-channel PWM LED strip driver, 24 V supply, 0.35 A per channel, controlled over I2C from a Raspberry Pi."),
    ("battery-charging", "Battery sensor node", "Li-ion cell, USB-C charging, weather sensor",
     "A battery-powered ESP32 sensor node: one Li-ion cell charged from USB-C, a BME280 sensor and an OLED connector."),
    ("cog", "Motor driver", "Two DC motors, forward and reverse",
     "An ESP32 board driving two brushed 12 V DC motors forward and reverse with PWM speed control, 1 A each. 12 V supply."),
    ("toggle-right", "Relay board", "Four relays for 24 V loads",
     "An ESP32 board with 4 relays switching 24 V DC valves at 2 A each, powered from 12 V."),
    ("droplets", "Greenhouse controller", "Humidity, valves and soil probes",
     "An ESP32 greenhouse board: SHT31 humidity sensor, two 12 V solenoid valves at 0.4 A, four 0-3 V analog inputs. 12 V supply."),
    ("cable", "RS-485 Modbus node", "Industrial bus with four outputs",
     "An RS-485 Modbus node with an ESP32, 24 V supply and 4 MOSFET outputs for 24 V lamps at 0.3 A each."),
    ("usb", "USB-C ESP32 board", "Programmed over USB-C, with sensor and display",
     "A USB-C powered ESP32 board programmed over the same USB-C port, with a BME280 sensor and an I2C OLED connector."),
]
STEPS = [("message-square-text", "Describe", "Say what the board should do. The engineer asks for anything missing."),
         ("clipboard-check", "Review", "Approve an architecture with parts, rules, risks and design checks."),
         ("hammer", "Build", "KiCad schematic and routed PCB, checked with ERC, DRC and datasheet limits."),
         ("package-check", "Order", "Gerbers, BOM and placement files ready for JLCPCB or PCBWay.")]


class _TemplateCard(QFrame):
    clicked = Signal(str)

    def __init__(self, icon_name, title, sub, prompt, parent=None):
        super().__init__(parent)
        self.prompt = prompt
        self.setObjectName("tile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(prompt)
        self.setFixedHeight(134)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 14)
        v.setSpacing(8)
        ic = QLabel()
        ic.setObjectName("tileIcon")
        ic.setFixedSize(36, 36)
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ic.setPixmap(icons.pixmap(icon_name, "#FFD700", 19))
        v.addWidget(ic)
        v.addWidget(label(title, "tileTitle"))
        d = label(sub, "tileSub", wrap=True)
        v.addWidget(d)
        v.addStretch(1)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.prompt)


class _Step(QWidget):
    def __init__(self, n, icon_name, title, sub, parent=None):
        super().__init__(parent)
        self.setObjectName("transparent")
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 0, 6, 0)
        v.setSpacing(8)
        ic = QLabel()
        ic.setObjectName("stepIcon")
        ic.setFixedSize(44, 44)
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ic.setPixmap(icons.pixmap(icon_name, "#22D3EE", 20))
        v.addWidget(ic, 0, Qt.AlignmentFlag.AlignHCenter)
        t = label(f"{n}  ·  {title}", "tileTitle")
        t.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(t)
        d = label(sub, "tileSub", wrap=True)
        d.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        v.addWidget(d)
        v.addStretch(1)


class HomeScreen(QWidget):
    new_project = Signal()
    start_with_prompt = Signal(str)
    open_project = Signal(str)
    open_example = Signal(str)
    open_settings = Signal()

    def __init__(self, branding, parent=None):
        super().__init__(parent)
        self.setObjectName("homeCanvas")
        from .motion import CircuitField, ShineTitle
        self.field = CircuitField(self)
        self._intro_done = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.viewport().setAutoFillBackground(False)
        sc.verticalScrollBar().valueChanged.connect(lambda _v: self.update())
        page = QWidget()
        page.setObjectName("transparent")
        sc.setWidget(page)
        outer.addWidget(sc)
        centre = QHBoxLayout(page)
        centre.setContentsMargins(40, 0, 40, 0)
        col = QWidget()
        col.setObjectName("transparent")
        col.setMaximumWidth(1080)
        centre.addStretch(1)
        centre.addWidget(col, 100)
        centre.addStretch(1)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 46, 0, 40)
        lay.setSpacing(0)
        C = Qt.AlignmentFlag.AlignHCenter

        # ---- hero, centred: mark, shining wordmark, one line
        hero = QWidget()
        hero.setObjectName("transparent")
        hv = QVBoxLayout(hero)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(6)
        self.mark = QLabel()
        self.mark.setPixmap(icons.logo(104))
        hv.addWidget(self.mark, 0, C)
        hv.addSpacing(6)
        a, b = (branding.wordmark + ["", ""])[:2]
        hv.addWidget(ShineTitle(a, b, 54), 0, C)
        tag = label(branding.tagline, "heroTag")
        hv.addWidget(tag, 0, C)
        self.hero = hero
        lay.addWidget(hero)
        lay.addSpacing(34)

        # ---- composer
        self.ask = label("What are we building?", "display")
        self.ask.setAlignment(C)
        lay.addWidget(self.ask)
        lay.addSpacing(14)
        comp = QFrame()
        comp.setObjectName("composer")
        cv = QVBoxLayout(comp)
        cv.setContentsMargins(18, 14, 14, 14)
        cv.setSpacing(10)
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("Describe the board: what it controls or measures, what powers it, how big it can be…")
        self.prompt.setFixedHeight(66)
        self.prompt.setStyleSheet("font-size:14px;")
        self.prompt.installEventFilter(self)
        cv.addWidget(self.prompt)
        row = QHBoxLayout()
        row.setSpacing(8)
        hint = label("Enter to start  ·  Shift+Enter for a new line", "dim")
        row.addWidget(hint)
        row.addStretch(1)
        self.start_btn = button("Start design", "primary", "arrow-right")
        self.start_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.start_btn.setMinimumHeight(38)
        self.start_btn.clicked.connect(self._start)
        row.addWidget(self.start_btn)
        cv.addLayout(row)
        comp.setProperty("focus", False)
        lay.addWidget(comp)
        self.composer = comp
        lay.addSpacing(12)
        exw = QWidget()
        exw.setObjectName("transparent")
        ex = QHBoxLayout(exw)
        ex.setContentsMargins(0, 0, 0, 0)
        ex.setSpacing(2)
        ex.addStretch(1)
        ex.addWidget(label("Or open a finished example:", "dim"))
        for title, key in EXAMPLES:
            b_ = button(title, "text")
            b_.clicked.connect(lambda _=False, k=key: self.open_example.emit(k))
            ex.addWidget(b_)
        ex.addStretch(1)
        lay.addWidget(exw)
        lay.addSpacing(42)

        # ---- start from a template (4 x 2)
        self.tpl_title = label("Start from a template", "title")
        self.tpl_title.setAlignment(C)
        lay.addWidget(self.tpl_title)
        lay.addSpacing(4)
        sub = label("Pick one, adjust the description, then start.", "dim")
        sub.setAlignment(C)
        lay.addWidget(sub)
        lay.addSpacing(16)
        tg = QWidget()
        tg.setObjectName("transparent")
        grid = QGridLayout(tg)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        from .motion import lift_on_hover
        self.tiles = []
        for k, (ic, t, s_, full) in enumerate(TEMPLATES):
            card = _TemplateCard(ic, t, s_, full)
            card.clicked.connect(self._use_template)
            lift_on_hover(card)
            grid.addWidget(card, k // 4, k % 4)
            self.tiles.append(card)
        for c_ in range(4):
            grid.setColumnStretch(c_, 1)
        lay.addWidget(tg)
        self.tpl_grid = tg
        lay.addSpacing(46)

        # ---- recent boards (only when there are some)
        self.recent_title = label("Recent boards", "title")
        self.recent_title.setAlignment(C)
        lay.addWidget(self.recent_title)
        lay.addSpacing(16)
        self.grid_host = QWidget()
        self.grid_host.setObjectName("transparent")
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(16)
        lay.addWidget(self.grid_host)
        self.recent_gap = QWidget()
        self.recent_gap.setFixedHeight(46)
        self.recent_gap.setObjectName("transparent")
        lay.addWidget(self.recent_gap)

        # ---- how it works
        hw_title = label("How it works", "title")
        hw_title.setAlignment(C)
        lay.addWidget(hw_title)
        lay.addSpacing(20)
        steps = QWidget()
        steps.setObjectName("transparent")
        sh = QHBoxLayout(steps)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(18)
        sh.setAlignment(Qt.AlignmentFlag.AlignTop)
        for n, (ic, t, d_) in enumerate(STEPS, 1):
            sh.addWidget(_Step(n, ic, t, d_), 1)
        lay.addWidget(steps)
        lay.addStretch(1)

        self._projects, self._thumb_for = [], None
        self.status_items = []
        self._intro = [hero, self.ask, comp, exw, self.tpl_title, tg, self.recent_title, self.grid_host, hw_title, steps]

    def _use_template(self, text: str):
        self.prompt.setPlainText(text)
        self.prompt.setFocus()
        cur = self.prompt.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        self.prompt.setTextCursor(cur)
        from .motion import fade_in
        fade_in(self.composer, ms=300, dy=0)

    # ------------------------------------------------------------- motion
    def paintEvent(self, e):
        qp = QPainter(self)
        qp.fillRect(self.rect(), QColor(INK))
        hp = self.mark.mapTo(self, self.mark.rect().center()) if self.mark.isVisible() else None
        self.field.paint(qp, self.width(), self.height(), QPointF(hp) if hp is not None else None)
        qp.end()

    def showEvent(self, e):
        super().showEvent(e)
        self.field.start()
        if not self._intro_done:
            self._intro_done = True
            from .motion import stagger
            stagger(self._intro, step=60, start=60, dy=14, ms=480)

    def hideEvent(self, e):
        self.field.stop()
        super().hideEvent(e)

    def eventFilter(self, obj, ev):
        if obj is self.prompt:
            if ev.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
                self.composer.setProperty("focus", ev.type() == QEvent.Type.FocusIn)
                self.composer.style().unpolish(self.composer)
                self.composer.style().polish(self.composer)
            elif ev.type() == QEvent.Type.KeyPress and ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                    and not ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._start()
                return True
        return super().eventFilter(obj, ev)

    def _start(self):
        t = self.prompt.toPlainText().strip()
        if t:
            self.prompt.clear()
            self.start_with_prompt.emit(t)
        else:
            self.new_project.emit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout_cards()

    def set_projects(self, projects: list[dict], thumb_for=None):
        self._projects, self._thumb_for = projects, thumb_for
        self._layout_cards(force=True)

    def _layout_cards(self, force=False):
        cols = max(1, min(3, (min(self.width() - 80, 1080) + 16) // (THUMB_W + 18)))
        if not force and getattr(self, "_cols", None) == cols:
            return
        self._cols = cols
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        has = bool(self._projects)
        for w in (self.recent_title, self.grid_host, self.recent_gap):
            w.setVisible(has)
        if not has:
            return
        items = self._projects[:6]
        n = min(cols, len(items))
        for c in range(cols + 2):
            self.grid.setColumnStretch(c, 0)
        self.grid.setColumnStretch(0, 1)                 # centred: stretch | cards | stretch
        self.grid.setColumnStretch(n + 1, 1)
        from .motion import lift_on_hover
        for i, p in enumerate(items):
            card = _ProjectCard(p, self._thumb_for(p["id"]) if self._thumb_for else None)
            card.clicked.connect(self.open_project)
            lift_on_hover(card)
            self.grid.addWidget(card, i // n, 1 + i % n, Qt.AlignmentFlag.AlignTop)

    def set_status(self, caps, ai_text: str, ai_ok: bool, router_ok: bool):
        """Setup status lives in the status bar and the first-run welcome; nothing on the home page."""
        return
