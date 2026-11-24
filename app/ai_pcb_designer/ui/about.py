"""About: version, licence, project links."""
from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout

from ..core.version import LICENSE, __version__, links
from .components import button, label


class AboutDialog(QDialog):
    def __init__(self, branding, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {branding.name}")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 20)
        lay.setSpacing(10)
        lay.addWidget(label(f"{branding.name}  {__version__} (beta)", "title"))
        lay.addWidget(label("AI-assisted PCB design on top of KiCad. Free software under the GNU General Public "
                            f"License ({LICENSE}). Includes Freerouting, Eclipse Temurin, Qt/PySide6 and other "
                            "components under their own licences (THIRD_PARTY_NOTICES.txt).", "muted", wrap=True))
        lay.addWidget(label("Boards are checked with KiCad's ERC/DRC and the app's design checks, but no circuit "
                            "simulation is run: review every design before ordering.", "dim", wrap=True))
        row = QHBoxLayout()
        lk = links()
        for text, key in (("Project page", "home"), ("Report a problem", "new_issue")):
            if lk.get(key):
                b = button(text, "row", "external-link")
                b.clicked.connect(lambda _=False, u=lk[key]: QDesktopServices.openUrl(QUrl(u)))
                row.addWidget(b)
        row.addStretch(1)
        ok = button("Close", "primary")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        lay.addLayout(row)
