"""Gerber Bear UI parts. One system: these are the only building blocks the screens use."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from . import icons
from .theme import GOLD, LINE, OK, TEAL, TEXT, TEXT2, TEXT3, WARN, ERR


def label(text: str = "", obj: str | None = None, wrap: bool = False) -> QLabel:
    l = QLabel(text)
    if obj:
        l.setObjectName(obj)
    l.setWordWrap(wrap)
    return l


def repolish(w: QWidget, obj: str | None = None):
    if obj:
        w.setObjectName(obj)
    w.style().unpolish(w)
    w.style().polish(w)


def hair(vertical=False) -> QFrame:
    f = QFrame()
    f.setObjectName("hair")
    if vertical:
        f.setFixedWidth(1)
    else:
        f.setFixedHeight(1)
    return f


class IconButton(QToolButton):
    def __init__(self, icon_name: str, tip: str, checkable=False, size=18, color=TEXT2, active=TEXT, parent=None):
        super().__init__(parent)
        self.setObjectName("icon")
        self.setIcon(icons.icon(icon_name, color, active, size=size))
        self.setIconSize(QSize(size, size))
        self.setToolTip(tip)
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(QSize(size + 14, size + 14))


def button(text: str, kind: str = "", icon_name: str | None = None, icon_color: str | None = None,
           icon_size: int = 16) -> QPushButton:
    """kind: '' (quiet), 'primary' (gold, one per screen), 'text', 'row', 'prompt'."""
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_name:
        col = icon_color or ("#14110A" if kind == "primary" else TEXT2)
        b.setIcon(icons.icon(icon_name, col, disabled=TEXT3, size=icon_size))
        b.setIconSize(QSize(icon_size, icon_size))
    if kind == "primary":
        from .motion import hover_glow
        hover_glow(b)
    return b


class Dot(QLabel):
    def __init__(self, color=OK, size=8, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.set_color(color)

    def set_color(self, color: str):
        self.setStyleSheet(f"background:{color}; border-radius:{self.width() // 2}px;")


def _wordmark(branding, gold: str) -> str:
    a, b = (branding.wordmark + ["", ""])[:2]
    return f"{a}&nbsp;<span style='color:{gold}'>{b}</span>" if b else a


class HeaderMark(QWidget):
    """Header lockup: bear + "Gerber Bear" set as one word pair (natural word space)."""

    def __init__(self, branding, parent=None):
        super().__init__(parent)
        from .theme import GOLD
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        lg = QLabel()
        lg.setPixmap(icons.logo(32))
        lg.setFixedSize(lg.pixmap().deviceIndependentSize().toSize())
        h.addWidget(lg, 0, Qt.AlignmentFlag.AlignVCenter)
        w = label(_wordmark(branding, GOLD), "brand")
        w.setTextFormat(Qt.TextFormat.RichText)
        w.setContentsMargins(0, 1, 0, 0)
        h.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)


class HeroLockup(QWidget):
    """Home lockup: large bear, name, one-line description under the name."""

    def __init__(self, branding, size=96, parent=None):
        super().__init__(parent)
        from .theme import GOLD
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(24)
        self.mark = QLabel()
        self.mark.setPixmap(icons.logo(size))
        h.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignVCenter)
        v = QVBoxLayout()
        v.setSpacing(4)
        v.addStretch(1)
        name = label(_wordmark(branding, GOLD), "heroBrand")
        name.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(name)
        t = label(branding.tagline, "heroTag")
        t.setContentsMargins(2, 0, 0, 0)
        v.addWidget(t)
        v.addStretch(1)
        h.addLayout(v)
        h.addStretch(1)


class Segmented(QFrame):
    """Compact segmented control; emits the selected key."""
    changed = Signal(str)

    def __init__(self, items: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setObjectName("segGroup")
        h = QHBoxLayout(self)
        h.setContentsMargins(3, 3, 3, 3)
        h.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for key, text in items:
            b = QPushButton(text)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.group.addButton(b)
            h.addWidget(b)
            self.buttons[key] = b
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        from .motion import SlidingPill
        self.pill = SlidingPill(self)
        self.pill.lower()
        self.group.buttonToggled.connect(lambda b, on: on and self._pill_to(b, True))

    def _pill_to(self, b, animate):
        self.pill.move_to(b.geometry(), animate)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        b = self.group.checkedButton()
        if b:
            self._pill_to(b, False)

    def showEvent(self, e):
        super().showEvent(e)
        b = self.group.checkedButton()
        if b:
            self._pill_to(b, False)

    def select(self, key: str, emit=True):
        if key in self.buttons:
            self.buttons[key].setChecked(True)
            if emit:
                self.changed.emit(key)


class Avatar(QLabel):
    def __init__(self, size=26, parent=None):
        super().__init__(parent)
        self.setObjectName("avatar")
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setPixmap(icons.logo(int(size * 0.66)))


class StatusLine(QWidget):
    """Dot + text, used for validation state and setup status."""

    def __init__(self, text="", tone="none", parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.dot = Dot(TEXT3, 8)
        self.text = label(text, "statusNone")
        h.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(self.text, 1)
        self.set(text, tone)

    def set(self, text: str, tone: str):
        col, obj = {"ok": (OK, "statusOk"), "warn": (WARN, "statusWarn"), "err": (ERR, "statusErr")}.get(
            tone, (TEXT3, "statusNone"))
        self.dot.set_color(col)
        self.text.setText(text)
        repolish(self.text, obj)


TONE_OF_STATUS = {"validated": "ok", "partially_validated": "ok", "validation_failed": "err",
                  "validation_unavailable": "warn", "generation_failed": "err", "cancelled": "warn"}
