"""First-run welcome: the two things a new user needs (KiCad and an AI key), each with its
real status, a link to get it and a way to finish setup, until both are in place."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import icons
from .components import button, label
from .theme import GOLD, LINE2, OK, RAISED, TEXT2, WARN

KICAD_URL = "https://www.kicad.org/download/windows/"
GEMINI_KEY_URL = "https://aistudio.google.com/apikey"


class WelcomeDialog(QDialog):
    def __init__(self, caps, has_key: bool, open_settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to Gerber Bear")
        self.setMinimumWidth(600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 30, 32, 24)
        lay.setSpacing(0)
        top = QHBoxLayout()
        top.setSpacing(16)
        mark = QLabel()
        mark.setPixmap(icons.logo(54))
        top.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        tv = QVBoxLayout()
        tv.setSpacing(4)
        t = label("Welcome to Gerber Bear", "display")
        t.setStyleSheet("font-size:22px;")
        tv.addWidget(t)
        tv.addWidget(label("Two things to set up once. Everything else is included.", "muted", wrap=True))
        top.addLayout(tv, 1)
        lay.addLayout(top)
        lay.addSpacing(24)

        kicad_ok = bool(caps and caps.installed and (caps.major or 0) >= 8)
        steps = [
            (1, "KiCad 8, 9 or 10", kicad_ok, f"KiCad {caps.version if caps else ''} is installed",
             "Not found. Install it with the default options, then restart Gerber Bear.",
             "Download KiCad", KICAD_URL),
            (2, "Your AI key", has_key, "Saved in Windows Credential Manager",
             "Get a free Gemini key from Google AI Studio and paste it in Settings → AI. It stays on this PC.",
             "Get a Gemini key", GEMINI_KEY_URL),
        ]
        cards = []
        for n, title, ok, ok_text, todo, link, url in steps:
            f = QFrame()
            f.setObjectName("block")
            f.setStyleSheet(f"QFrame#block {{ background:{RAISED}; border:1px solid {LINE2}; border-radius:14px; }}")
            h = QHBoxLayout(f)
            h.setContentsMargins(16, 14, 16, 14)
            h.setSpacing(14)
            num = QLabel("✓" if ok else str(n))
            num.setFixedSize(28, 28)
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col = OK if ok else GOLD
            num.setStyleSheet(f"border-radius:14px; border:1.5px solid {col}; color:{col}; font-weight:700;"
                              "background:transparent;")
            h.addWidget(num, 0, Qt.AlignmentFlag.AlignTop)
            v = QVBoxLayout()
            v.setSpacing(3)
            v.addWidget(label(title, "title"))
            s = label(ok_text if ok else todo, "muted", wrap=True)
            s.setStyleSheet(f"color:{OK if ok else TEXT2};")
            v.addWidget(s)
            h.addLayout(v, 1)
            if not ok:
                b = button(link, "", "external-link")
                b.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
                h.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
            lay.addWidget(f)
            lay.addSpacing(10)
            cards.append(f)
        lay.addSpacing(14)
        row = QHBoxLayout()
        row.addStretch(1)
        later = button("Later", "text")
        later.clicked.connect(self.reject)
        row.addWidget(later)
        if not has_key:
            go = button("Open Settings", "primary", "settings")
            go.clicked.connect(lambda: (self.accept(), open_settings()))
            row.addWidget(go)
        else:
            done = button("Start designing", "primary", "arrow-right")
            done.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            done.clicked.connect(self.accept)
            row.addWidget(done)
        lay.addLayout(row)
        _ = WARN
        from .motion import stagger
        stagger(cards, step=90, start=120, dy=10)
