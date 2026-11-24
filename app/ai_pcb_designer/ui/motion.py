"""Motion for Gerber Bear: small, purposeful animations.

Everything moves for a reason - something arrived, something is working, something
finished, focus moved. Durations are short (150-450 ms) with ease-out curves, nothing
loops unless work is in progress, and Settings > Interface > "Reduce motion" turns it all
into instant changes.
"""
from __future__ import annotations

import math
import random

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QParallelAnimationGroup, QPointF, QPropertyAnimation, QRect, QRectF,
                            QSequentialAnimationGroup, Qt, QTimer, QVariantAnimation, Property)
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QLabel, QStackedWidget, QWidget

from .theme import GOLD, LINE, LINE2, OK, ERR, TEAL, TEAL_SOFT, TEXT3

ENABLED = True
OUT = QEasingCurve.Type.OutCubic


def set_enabled(on: bool):
    global ENABLED
    ENABLED = bool(on)


def _keep(owner, anim):
    """Keep a Python reference until the animation ends (PySide would collect it)."""
    lst = owner.__dict__.setdefault("_anims", [])
    lst.append(anim)
    anim.finished.connect(lambda: anim in lst and lst.remove(anim))
    return anim


# ----------------------------------------------------------------------------- entrances
def fade_in(w: QWidget, ms: int = 320, delay: int = 0, dy: int = 10):
    """Fade a widget in while it slides up `dy` px (via its layout margin)."""
    if not ENABLED:
        return
    eff = QGraphicsOpacityEffect(w)
    eff.setOpacity(0.0)
    w.setGraphicsEffect(eff)
    lay = w.layout()
    m = lay.contentsMargins() if lay else None
    fade = QPropertyAnimation(eff, b"opacity", w)
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    fade.setDuration(ms)
    fade.setEasingCurve(OUT)
    group = QParallelAnimationGroup(w)
    group.addAnimation(fade)
    if lay is not None and dy:
        slide = QVariantAnimation(w)
        slide.setStartValue(float(dy))
        slide.setEndValue(0.0)
        slide.setDuration(ms)
        slide.setEasingCurve(OUT)
        slide.valueChanged.connect(lambda v: lay.setContentsMargins(m.left(), m.top() + int(v), m.right(),
                                                                    max(0, m.bottom() - int(v))))
        group.addAnimation(slide)

    def done():
        try:
            w.setGraphicsEffect(None)        # effects cost a repaint path; drop when finished
        except RuntimeError:
            pass
    group.finished.connect(done)
    if delay:
        seq = QSequentialAnimationGroup(w)
        seq.addPause(delay)
        seq.addAnimation(group)
        _keep(w, seq).start()
    else:
        _keep(w, group).start()


def stagger(widgets, step: int = 60, start: int = 0, dy: int = 12, ms: int = 420):
    for i, w in enumerate(widgets):
        fade_in(w, ms=ms, delay=start + i * step, dy=dy)


class _Hover(QObject):
    def __init__(self, w, on_enter, on_leave):
        super().__init__(w)
        self.on_enter, self.on_leave = on_enter, on_leave
        w.installEventFilter(self)

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.Enter:
            self.on_enter()
        elif t == QEvent.Type.Leave:
            self.on_leave()
        return False


def hover_glow(w: QWidget, color: str = GOLD, radius: float = 26, alpha: int = 120):
    """Soft light around a button on hover (primary actions only)."""
    eff = QGraphicsDropShadowEffect(w)
    c = QColor(color)
    c.setAlpha(alpha)
    eff.setColor(c)
    eff.setOffset(0, 0)
    eff.setBlurRadius(0)
    w.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b"blurRadius", w)
    anim.setDuration(220)
    anim.setEasingCurve(OUT)

    def to(v):
        anim.stop()
        if not ENABLED:
            eff.setBlurRadius(v)
            return
        anim.setStartValue(eff.blurRadius()); anim.setEndValue(v); anim.start()
    w.__dict__["_glow"] = _Hover(w, lambda: w.isEnabled() and to(radius), lambda: to(0.0))


def lift_on_hover(w: QWidget):
    """Cards rise slightly (deeper shadow) under the pointer."""
    eff = QGraphicsDropShadowEffect(w)
    eff.setColor(QColor(0, 0, 0, 150))
    eff.setOffset(0, 2)
    eff.setBlurRadius(8)
    w.setGraphicsEffect(eff)
    blur = QPropertyAnimation(eff, b"blurRadius", w)
    off = QVariantAnimation(w)
    off.valueChanged.connect(lambda v: eff.setOffset(0, v))
    for a in (blur, off):
        a.setDuration(240)
        a.setEasingCurve(OUT)

    def go(b_to, o_to):
        if not ENABLED:
            eff.setBlurRadius(b_to); eff.setOffset(0, o_to); return
        blur.stop(); blur.setStartValue(eff.blurRadius()); blur.setEndValue(b_to); blur.start()
        off.stop(); off.setStartValue(float(eff.offset().y())); off.setEndValue(float(o_to)); off.start()
    w.__dict__["_lift"] = _Hover(w, lambda: go(30, 10), lambda: go(8, 2))


def count_up(lbl: QLabel, text: str, ms: int = 650):
    """Animate the numbers inside `text` from 0 (e.g. '110 × 85 mm', '460 / 29')."""
    nums = [float(x) for x in __import__("re").findall(r"\d+(?:\.\d+)?", text)]
    if not ENABLED or not nums:
        lbl.setText(text)
        return
    import re
    parts = re.split(r"(\d+(?:\.\d+)?)", text)
    a = QVariantAnimation(lbl)
    a.setStartValue(0.0)
    a.setEndValue(1.0)
    a.setDuration(ms)
    a.setEasingCurve(OUT)

    def upd(k):
        out, i = [], 0
        for p in parts:
            if re.fullmatch(r"\d+(?:\.\d+)?", p):
                dec = len(p.split(".")[1]) if "." in p else 0
                out.append(f"{nums[i] * k:.{dec}f}")
                i += 1
            else:
                out.append(p)
        lbl.setText("".join(out))
    a.valueChanged.connect(upd)
    a.finished.connect(lambda: lbl.setText(text))
    _keep(lbl, a).start()


# ----------------------------------------------------------------------------- indicators
class Spinner(QWidget):
    """A small rotating arc: work in progress."""

    def __init__(self, size: int = 14, color: str = TEAL_SOFT, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._a, self._c = 0.0, QColor(color)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(360.0)
        self._anim.setDuration(900)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._set)
        if ENABLED:
            self._anim.start()

    def _set(self, v):
        self._a = v
        self.update()

    def paintEvent(self, _):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1.5, 1.5, self.width() - 3, self.height() - 3)
        track = QColor(self._c)
        track.setAlpha(45)
        qp.setPen(QPen(track, 2))
        qp.drawEllipse(r)
        qp.setPen(_pen(self._c, 2))
        qp.drawArc(r, int(-self._a * 16), 100 * 16)
        qp.end()


class StepIcon(QWidget):
    """Progress step marker: spinning while running, a check that draws itself on success."""

    def __init__(self, parent=None, size: int = 16, state: str = "running"):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._scale = size / 16.0
        self.state, self._k, self._rot = state, 0.0, 0.0
        self._spin = QVariantAnimation(self)
        self._spin.setStartValue(0.0)
        self._spin.setEndValue(360.0)
        self._spin.setDuration(900)
        self._spin.setLoopCount(-1)
        self._spin.valueChanged.connect(lambda v: (setattr(self, "_rot", v), self.update()))
        if ENABLED and state == "running":
            self._spin.start()

    def finish(self, ok: bool):
        self._spin.stop()
        self.state = "ok" if ok else "fail"
        if not ENABLED:
            self._k = 1.0
            self.update()
            return
        a = QVariantAnimation(self)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setDuration(380)
        a.setEasingCurve(QEasingCurve.Type.OutBack)
        a.valueChanged.connect(lambda v: (setattr(self, "_k", v), self.update()))
        _keep(self, a).start()

    def paintEvent(self, _):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.scale(self._scale, self._scale)
        c = QRectF(2, 2, 12, 12)
        if self.state == "done":                    # waiting to play its finish animation
            qp.end()
            return
        if self.state == "running":
            t = QColor(TEAL_SOFT); t.setAlpha(45)
            qp.setPen(QPen(t, 1.8)); qp.drawEllipse(c)
            qp.setPen(_pen(QColor(TEAL_SOFT), 1.8))
            qp.drawArc(c, int(-self._rot * 16), 110 * 16)
        else:
            col = QColor(OK if self.state == "ok" else ERR)
            k = max(0.0, self._k)
            s = min(1.0, k * 1.4)
            fill = QColor(col); fill.setAlpha(int(40 * min(1, k)))
            qp.setPen(Qt.PenStyle.NoPen); qp.setBrush(fill)
            qp.drawEllipse(QPointF(8, 8), 7 * s, 7 * s)
            qp.setPen(_pen(col, 1.8))
            qp.setBrush(Qt.BrushStyle.NoBrush)
            if self.state == "ok":
                path = QPainterPath(QPointF(4.8, 8.3))
                path.lineTo(7.1, 10.6)
                path.lineTo(11.4, 5.6)
                qp.drawPath(_partial(path, min(1.0, k)))
            else:
                d = 2.8 * min(1.0, k)
                qp.drawLine(QPointF(8 - d, 8 - d), QPointF(8 + d, 8 + d))
                qp.drawLine(QPointF(8 + d, 8 - d), QPointF(8 - d, 8 + d))
        qp.end()


def _pen(color, width):
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _partial(path: QPainterPath, k: float) -> QPainterPath:
    if k >= 1:
        return path
    out = QPainterPath(path.pointAtPercent(0))
    n = 24
    for i in range(1, int(n * k) + 1):
        out.lineTo(path.pointAtPercent(i / n))
    return out


class FlowBar(QWidget):
    """Indeterminate progress: a teal-to-gold comet running along a hairline track."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(3)
        self._x, self._done = 0.0, False
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(-0.35)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1500)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(lambda v: (setattr(self, "_x", v), self.update()))
        if ENABLED:
            self._anim.start()

    def finish(self):
        self._anim.stop()
        self._done = True
        self.update()

    def paintEvent(self, _):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(QColor(LINE))
        qp.drawRoundedRect(QRectF(0, 0, w, h), 1.5, 1.5)
        if self._done:
            qp.setBrush(QColor(OK))
            qp.drawRoundedRect(QRectF(0, 0, w, h), 1.5, 1.5)
        else:
            x0, seg = self._x * w, 0.35 * w
            g = QLinearGradient(x0, 0, x0 + seg, 0)
            t0 = QColor(TEAL); t0.setAlpha(0)
            g.setColorAt(0, t0)
            g.setColorAt(0.7, QColor(TEAL_SOFT))
            g.setColorAt(1, QColor(GOLD))
            qp.setBrush(g)
            qp.drawRoundedRect(QRectF(max(0, x0), 0, min(seg, w - max(0, x0)), h), 1.5, 1.5)
        qp.end()


class TypingDots(QWidget):
    """Three dots in a wave: the assistant is writing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(34, 14)
        self._t = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1100)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(lambda v: (setattr(self, "_t", v), self.update()))

    def showEvent(self, e):
        if ENABLED:
            self._anim.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._anim.stop()
        super().hideEvent(e)

    def paintEvent(self, _):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            ph = (self._t - i * 0.16) % 1.0
            lift = math.sin(ph * math.pi) if ph < 0.5 else 0.0
            c = QColor(TEAL_SOFT)
            c.setAlphaF(0.35 + 0.65 * lift)
            qp.setBrush(c)
            qp.drawEllipse(QPointF(6 + i * 11, 9 - 4 * lift), 3, 3)
        qp.end()


class PulseDot(QWidget):
    """A status dot that breathes while something is running."""

    def __init__(self, color: str = TEAL_SOFT, size: int = 8, parent=None):
        super().__init__(parent)
        self.setFixedSize(size + 10, size + 10)
        self._c, self._s, self._k, self._run = QColor(color), size, 0.0, False
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1400)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(lambda v: (setattr(self, "_k", v), self.update()))

    def set(self, color: str, running: bool):
        self._c = QColor(color)
        self._run = running and ENABLED
        (self._anim.start() if self._run else self._anim.stop())
        self.update()

    def paintEvent(self, _):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        if self._run:
            halo = QColor(self._c)
            halo.setAlphaF(0.45 * (1 - self._k))
            qp.setPen(Qt.PenStyle.NoPen)
            qp.setBrush(halo)
            r = self._s / 2 + 5 * self._k
            qp.drawEllipse(QPointF(cx, cy), r, r)
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(self._c)
        qp.drawEllipse(QPointF(cx, cy), self._s / 2, self._s / 2)
        qp.end()


# ----------------------------------------------------------------------------- page transitions
class FadeStack(QStackedWidget):
    """QStackedWidget that cross-fades: a snapshot of the old page fades out over the new one."""

    def __init__(self, parent=None, ms: int = 260):
        super().__init__(parent)
        self._ms = ms
        self._overlay = None

    def setCurrentIndex(self, i: int):
        cur = self.currentWidget()
        if (not ENABLED or i == self.currentIndex() or not self.isVisible() or cur is None
                or getattr(cur, "no_fade", False)):          # pages with web views do not snapshot well
            super().setCurrentIndex(i)
            return
        snap = self.currentWidget().grab()
        super().setCurrentIndex(i)
        ov = QLabel(self)
        ov.setPixmap(snap)
        ov.setGeometry(0, 0, self.width(), self.height())
        ov.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        eff = QGraphicsOpacityEffect(ov)
        ov.setGraphicsEffect(eff)
        ov.show()
        ov.raise_()
        a = QPropertyAnimation(eff, b"opacity", ov)
        a.setStartValue(1.0)
        a.setEndValue(0.0)
        a.setDuration(self._ms)
        a.setEasingCurve(OUT)
        a.finished.connect(ov.deleteLater)
        _keep(self, a).start()

    def setCurrentWidget(self, w):
        self.setCurrentIndex(self.indexOf(w))


class SlidingPill(QWidget):
    """The highlight behind a segmented control's selected button; glides between buttons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.lower()
        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(OUT)

    def move_to(self, rect: QRect, animate=True):
        if not animate or not ENABLED or self.geometry().isEmpty():
            self.setGeometry(rect)
            return
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(rect)
        self._anim.start()

    def paintEvent(self, _):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(QColor("#1E2A45"))
        qp.drawRoundedRect(QRectF(self.rect()), 8, 8)
        # a hairline of teal along the bottom edge
        g = QLinearGradient(0, 0, self.width(), 0)
        c0 = QColor(TEAL_SOFT); c0.setAlpha(0)
        g.setColorAt(0, c0); g.setColorAt(0.5, QColor(TEAL_SOFT)); g.setColorAt(1, c0)
        qp.setBrush(g)
        qp.drawRoundedRect(QRectF(10, self.height() - 2, self.width() - 20, 2), 1, 1)
        qp.end()


# ----------------------------------------------------------------------------- home backdrop
class CircuitField:
    """Faint PCB traces across the home screen with signal pulses travelling along them.
    Drawn by the owning widget's paintEvent; ~30 fps only while visible."""

    def __init__(self, owner: QWidget, seed: int = 7):
        self.owner = owner
        self.rng = random.Random(seed)
        self.traces: list[list[QPointF]] = []
        self.pulses: list[list] = []
        self.size = None
        self.timer = QTimer(owner)
        self.timer.timeout.connect(self._tick)
        self.t = 0.0
        self.twinkles = Twinkles()

    def start(self):
        if ENABLED and not self.timer.isActive():
            self.timer.start(33)

    def stop(self):
        self.timer.stop()

    def _build(self, w: int, h: int):
        rng = self.rng
        self.traces = []
        grid = 28
        for _ in range(int(w * h / 26000)):
            x = rng.randrange(0, w // grid) * grid
            y = rng.randrange(0, h // grid) * grid
            pts = [QPointF(x, y)]
            d = rng.choice([(1, 0), (-1, 0), (0, 1), (0, -1)])
            for _seg in range(rng.randint(2, 4)):
                n = rng.randint(2, 7) * grid
                x += d[0] * n
                y += d[1] * n
                pts.append(QPointF(x, y))
                if rng.random() < 0.7:       # 45-degree bend, like a real trace
                    s = rng.choice([-1, 1]) * grid
                    if d[0]:
                        x += d[0] * abs(s); y += s
                    else:
                        x += s; y += d[1] * abs(s)
                    pts.append(QPointF(x, y))
                d = (d[1], d[0]) if rng.random() < 0.5 else d
            self.traces.append(pts)
        self.pulses = [[rng.randrange(len(self.traces)), rng.random(), 0.06 + rng.random() * 0.08,
                        rng.random() < 0.3] for _ in range(max(6, len(self.traces) // 4))] if self.traces else []
        self.size = (w, h)

    def _tick(self):
        if not self.owner.isVisible():
            self.stop()
            return
        self.t += 0.033
        for p in self.pulses:
            p[1] += p[2] * 0.033
            if p[1] > 1.0:
                p[0], p[1] = self.rng.randrange(len(self.traces)), 0.0
        self.owner.update()

    @staticmethod
    def _point_at(pts, k):
        lens = [math.dist((a.x(), a.y()), (b.x(), b.y())) for a, b in zip(pts, pts[1:])]
        total = sum(lens) or 1
        d = k * total
        for (a, b), L in zip(zip(pts, pts[1:]), lens):
            if d <= L:
                f = d / (L or 1)
                return QPointF(a.x() + (b.x() - a.x()) * f, a.y() + (b.y() - a.y()) * f)
            d -= L
        return pts[-1]

    def paint(self, qp: QPainter, w: int, h: int, focus: QPointF | None = None):
        if self.size != (w, h):
            self._build(w, h)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        # soft light behind the hero
        if focus is not None:
            g = QRadialGradient(focus, max(w, h) * 0.45)
            c = QColor(TEAL); c.setAlpha(26)
            g.setColorAt(0, c)
            c2 = QColor(GOLD); c2.setAlpha(8)
            g.setColorAt(0.45, c2)
            g.setColorAt(1, QColor(0, 0, 0, 0))
            qp.fillRect(0, 0, w, h, g)
        line = QColor(LINE2)
        line.setAlpha(48)
        qp.setPen(QPen(line, 1.2))
        pad = QColor(LINE2)
        pad.setAlpha(80)
        for pts in self.traces:
            for a, b in zip(pts, pts[1:]):
                qp.drawLine(a, b)
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(pad)
        for pts in self.traces:
            qp.drawEllipse(pts[0], 2.6, 2.6)
            qp.drawEllipse(pts[-1], 2.6, 2.6)
        self.twinkles.paint(qp, w, h, self.t, focus)
        for idx, k, _v, gold in self.pulses:
            if idx >= len(self.traces):
                continue
            p = self._point_at(self.traces[idx], k)
            col = QColor(GOLD if gold else TEAL_SOFT)
            fade = math.sin(k * math.pi)
            g = QRadialGradient(p, 10)
            c = QColor(col); c.setAlphaF(0.35 * fade)
            g.setColorAt(0, c)
            g.setColorAt(1, QColor(0, 0, 0, 0))
            qp.setBrush(g)
            qp.drawEllipse(p, 10, 10)
            c.setAlphaF(0.9 * fade)
            qp.setBrush(c)
            qp.drawEllipse(p, 1.8, 1.8)


# ----------------------------------------------------------------------------- shine
class ShineTitle(QWidget):
    """The wordmark, painted: 'Gerber' in white, 'Bear' in gold, with a band of light that
    sweeps across it every few seconds."""

    def __init__(self, first: str, second: str, size: int = 52, parent=None):
        super().__init__(parent)
        from PySide6.QtGui import QFont, QFontMetrics
        self.a, self.b = first, second
        self.font = QFont("Inter")
        self.font.setPixelSize(size)
        self.font.setWeight(QFont.Weight.ExtraBold)
        self.font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -size * 0.025)
        fm = QFontMetrics(self.font)
        self.wa = fm.horizontalAdvance(first + " ")
        self.setFixedSize(self.wa + fm.horizontalAdvance(second) + 8, fm.height() + 6)
        self._x = -1.0
        self._sweep = QVariantAnimation(self)
        self._sweep.setStartValue(-0.3)
        self._sweep.setEndValue(1.3)
        self._sweep.setDuration(1400)
        self._sweep.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._sweep.valueChanged.connect(lambda v: (setattr(self, "_x", v), self.update()))
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: ENABLED and self._sweep.start())
        self._timer.start(4800)
        QTimer.singleShot(900, lambda: ENABLED and self._sweep.start())

    def paintEvent(self, _):
        from PySide6.QtGui import QFontMetrics
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        qp.setFont(self.font)
        base = QFontMetrics(self.font).ascent() + 2
        w = self.width()
        cx = self._x * w
        for text, x0, col in ((self.a, 0, QColor("#E7ECF4")), (self.b, self.wa, QColor(GOLD))):
            g = QLinearGradient(cx - 70, 0, cx + 70, 0)
            hi = QColor("#FFFFFF") if col.name() != QColor(GOLD).name() else QColor("#FFF6C2")
            g.setColorAt(0, col)
            g.setColorAt(0.5, hi if 0 <= self._x <= 1.2 else col)
            g.setColorAt(1, col)
            pen = QPen()
            pen.setBrush(g)
            qp.setPen(pen)
            qp.drawText(QPointF(x0, base), text)
        qp.end()


class Twinkles:
    """Small four-point stars that fade in and out: a little sparkle on the home screen."""

    def __init__(self, n: int = 26, seed: int = 3):
        self.rng = random.Random(seed)
        self.n = n
        self.stars = []
        self.size = None

    def paint(self, qp: QPainter, w: int, h: int, t: float, around: QPointF | None = None):
        if self.size != (w, h):
            self.size = (w, h)
            self.stars = []
            for i in range(self.n):
                if around is not None and i < 8:      # a few near the logo
                    x = around.x() + self.rng.uniform(-110, 110)
                    y = around.y() + self.rng.uniform(-70, 70)
                else:
                    x, y = self.rng.uniform(0, w), self.rng.uniform(0, h)
                self.stars.append((x, y, self.rng.uniform(1.6, 3.6), self.rng.uniform(0, 1),
                                   self.rng.uniform(0.18, 0.4), self.rng.random() < 0.55))
        qp.setPen(Qt.PenStyle.NoPen)
        for x, y, r, ph, speed, gold in self.stars:
            k = math.sin(((t * speed + ph) % 1.0) * math.pi) ** 3
            if k < 0.02:
                continue
            col = QColor(GOLD if gold else TEAL_SOFT)
            col.setAlphaF(0.85 * k)
            s = r * (0.6 + 0.8 * k)
            path = QPainterPath(QPointF(x, y - s * 2.2))
            for px, py in ((x + s * 0.35, y - s * 0.35), (x + s * 2.2, y), (x + s * 0.35, y + s * 0.35),
                           (x, y + s * 2.2), (x - s * 0.35, y + s * 0.35), (x - s * 2.2, y),
                           (x - s * 0.35, y - s * 0.35)):
                path.lineTo(px, py)
            path.closeSubpath()
            glow = QRadialGradient(QPointF(x, y), s * 4)
            gc = QColor(col)
            gc.setAlphaF(0.25 * k)
            glow.setColorAt(0, gc)
            glow.setColorAt(1, QColor(0, 0, 0, 0))
            qp.setBrush(glow)
            qp.drawEllipse(QPointF(x, y), s * 4, s * 4)
            qp.setBrush(col)
            qp.drawPath(path)
