"""Line icons (Lucide, ISC licence) recoloured at runtime, plus the brand logo as pixmaps."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ..core.config import package_root

ICON_DIR = package_root() / "resources" / "icons"
BRAND_DIR = package_root() / "resources" / "branding"


def _svg_bytes(name: str, color: str, stroke: float | None = None) -> bytes:
    p = ICON_DIR / f"{name}.svg"
    txt = p.read_text(encoding="utf-8") if p.exists() else (ICON_DIR / "square.svg").read_text(encoding="utf-8")
    txt = txt.replace("currentColor", color)
    if stroke:
        txt = txt.replace('stroke-width="2"', f'stroke-width="{stroke}"')
    return txt.encode()


@lru_cache(maxsize=512)
def pixmap(name: str, color: str = "#8693AD", size: int = 18, stroke: float | None = 1.75, dpr: float = 2.0) -> QPixmap:
    r = QSvgRenderer(QByteArray(_svg_bytes(name, color, stroke)))
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    qp = QPainter(pm)
    qp.setRenderHint(QPainter.RenderHint.Antialiasing)
    r.render(qp, QRectF(0, 0, size * dpr, size * dpr))
    qp.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str = "#8693AD", active: str | None = None, disabled: str = "#3A4763",
         size: int = 18) -> QIcon:
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(pixmap(name, active or color, size), QIcon.Mode.Active, QIcon.State.Off)
    ic.addPixmap(pixmap(name, active or color, size), QIcon.Mode.Normal, QIcon.State.On)
    ic.addPixmap(pixmap(name, disabled, size), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic


@lru_cache(maxsize=64)
def logo(size: int, variant: str = "gerber_bear_logo", dpr: float = 2.0) -> QPixmap:
    """Brand mark rendered from its SVG; keeps the aspect ratio inside a size x size box."""
    r = QSvgRenderer(str(BRAND_DIR / f"{variant}.svg"))
    vb = r.viewBoxF()
    k = size / max(vb.width(), vb.height())
    w, h = vb.width() * k, vb.height() * k
    pm = QPixmap(int(w * dpr), int(h * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    qp = QPainter(pm)
    qp.setRenderHint(QPainter.RenderHint.Antialiasing)
    qp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    r.render(qp, QRectF(0, 0, w * dpr, h * dpr))
    qp.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def app_icon() -> QIcon:
    ico = BRAND_DIR / "gerber_bear.ico"
    if ico.exists():
        return QIcon(str(ico))
    ic = QIcon()
    for s in (16, 32, 64, 128, 256):
        ic.addPixmap(logo(s, "gerber_bear_app_icon", 1.0))
    return ic


ICON_SIZE = QSize(18, 18)
