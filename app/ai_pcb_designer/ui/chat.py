"""Conversation pane: a calm thread. The assistant writes on the pane itself; the user's
messages sit in quiet blocks; proposals, progress and results are document blocks."""
from __future__ import annotations

import html
import textwrap

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import icons
from .components import Avatar, IconButton, StatusLine, TONE_OF_STATUS, button, hair, label
from .theme import TEXT2, TEXT3, TEAL_SOFT, OK, ERR, WARN

STATUS_LABEL = {
    "validated": ("Validated: DRC and ERC clean", "badgeOk"),
    "partially_validated": ("DRC clean, ERC has findings", "badgeOk"),
    "validation_failed": ("Checks failed", "badgeErr"),
    "validation_unavailable": ("Checks unavailable", "badgeWarn"),
    "generation_failed": ("Generation failed", "badgeErr"),
    "cancelled": ("Cancelled", "badgeWarn"),
}
STEP_NAMES = {"schematic": "Schematic", "netlist_verification": "Netlist check", "erc": "Electrical rules",
              "placement": "Placement", "pcb_build": "Board build", "planes": "Power planes",
              "plane_fanout": "Plane fan-out", "neck_down": "Pin neck-down", "usb_fanout": "USB-C fan-out", "routing": "Routing", "copper_pours": "Copper pours",
              "drc": "Design rules", "labels": "Silkscreen labels", "repair": "Repair", "exports": "Exports"}


def _text(text: str, md=True, obj=None, color: str | None = None) -> QLabel:
    lab = QLabel(text)
    lab.setTextFormat(Qt.TextFormat.MarkdownText if md else Qt.TextFormat.RichText)
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse)
    lab.setOpenExternalLinks(True)
    lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    css = "background:transparent; border:none;"
    if color:
        css += f" color:{color};"
    lab.setStyleSheet(css)
    if obj:
        lab.setObjectName(obj)
    return lab


def _block(obj="block", m=(16, 14, 16, 14)) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName(obj)
    lay = QVBoxLayout(f)
    lay.setContentsMargins(*m)
    lay.setSpacing(8)
    return f, lay


class _Composer(QFrame):
    def __init__(self, placeholder="Message Gerber Bear…", parent=None):
        super().__init__(parent)
        self.setObjectName("composer")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 10, 10)
        v.setSpacing(6)
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.edit.setFixedHeight(44)
        self.edit.document().contentsChanged.connect(self._fit)
        self.edit.installEventFilter(self)
        v.addWidget(self.edit)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.hint = label("Enter to send, Shift+Enter for a new line", "dim")
        row.addWidget(self.hint, 1)
        self.send = QPushButton()
        self.send.setObjectName("send")
        self.send.setFixedSize(32, 32)
        self.send.setIcon(icons.icon("send", "#04121A", size=15))
        self.send.setIconSize(QSize(15, 15))
        self.send.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send.setToolTip("Send")
        row.addWidget(self.send)
        v.addLayout(row)

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.setProperty("focus", ev.type() == QEvent.Type.FocusIn)
            self.style().unpolish(self)
            self.style().polish(self)
        return super().eventFilter(obj, ev)

    def _fit(self):
        lines = max(2, min(7, int(self.edit.document().size().height())))
        self.edit.setFixedHeight(min(150, 8 + lines * 19))


class ChatPanel(QWidget):
    send_message = Signal(str)
    approve = Signal()
    reject = Signal()
    request_changes = Signal()
    cancel_job = Signal()
    open_tab = Signal(str)
    close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pane")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        head = QWidget()
        head.setObjectName("transparent")
        head.setFixedHeight(52)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(18, 0, 10, 0)
        hl.setSpacing(10)
        hl.addWidget(label("Conversation", "title"), 0, Qt.AlignmentFlag.AlignVCenter)
        self.provider_badge = label("", "dim")
        hl.addWidget(self.provider_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        self.sim_badge = label("Simulated", "simChip")
        self.sim_badge.setToolTip("Development stand-in, not a real AI (AIPCB_DEV_SCRIPTED_AI=1).")
        self.sim_badge.hide()
        hl.addWidget(self.sim_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        hl.addStretch(1)
        close = IconButton("panel-left", "Hide conversation", size=16)
        close.clicked.connect(self.close_requested)
        hl.addWidget(close)
        root.addWidget(head)
        root.addWidget(hair())

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        self.inner = QWidget()
        self.inner.setObjectName("scrollInner")
        self.vbox = QVBoxLayout(self.inner)
        self.vbox.setContentsMargins(18, 18, 18, 12)
        self.vbox.setSpacing(18)
        self.vbox.addStretch(1)
        self.scroll.setWidget(self.inner)
        self._follow = True
        sb = self.scroll.verticalScrollBar()
        sb.rangeChanged.connect(lambda lo, hi: self._follow and sb.setValue(hi))
        sb.valueChanged.connect(lambda v: setattr(self, "_follow", v >= sb.maximum() - 40))
        root.addWidget(self.scroll, 1)

        from .motion import TypingDots
        self.thinking = QWidget()
        self.thinking.setObjectName("transparent")
        tl = QHBoxLayout(self.thinking)
        tl.setContentsMargins(20, 0, 16, 6)
        tl.setSpacing(8)
        self._dots_w = TypingDots()
        tl.addWidget(self._dots_w, 0, Qt.AlignmentFlag.AlignVCenter)
        self._think_text = label("", "dim")
        tl.addWidget(self._think_text, 1, Qt.AlignmentFlag.AlignVCenter)
        self.thinking.hide()
        root.addWidget(self.thinking)

        box = QWidget()
        box.setObjectName("transparent")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(14, 4, 14, 14)
        self.composer = _Composer()
        self.input = self.composer.edit
        self.input.installEventFilter(self)
        self.send_btn = self.composer.send
        self.send_btn.clicked.connect(self._send)
        self.hint = self.composer.hint
        bl.addWidget(self.composer)
        root.addWidget(box)
        self.progress_card = None
        self._busy = False
        self._busy_text = "Thinking"

    # ------------------------------------------------------------------ input
    def eventFilter(self, obj, ev):
        if obj is self.input and ev.type() == QEvent.Type.KeyPress and ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and not ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self._send()
            return True
        return super().eventFilter(obj, ev)

    def _send(self):
        t = self.input.toPlainText().strip()
        if not t or self._busy:
            return
        self.input.clear()
        self.send_message.emit(t)

    def set_busy(self, busy: bool, text="Thinking"):
        self._busy = busy
        self._busy_text = text.rstrip("…. ")
        self.send_btn.setEnabled(not busy)
        self._think_text.setText(self._busy_text)
        self.thinking.setVisible(busy)

    def set_provider(self, label_: str, simulated: bool, configured: bool):
        self.provider_badge.setText(label_.split(" · ")[-1] if configured and label_ else "AI not set up")
        self.provider_badge.setToolTip(label_)
        self.sim_badge.setVisible(simulated)

    # --------------------------------------------------------------- messages
    def clear(self):
        while self.vbox.count() > 1:
            w = self.vbox.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.progress_card = None
        self._proposal_buttons = ()

    def _row(self, w: QWidget, who: str):
        row = QWidget()
        row.setObjectName("transparent")
        l = QHBoxLayout(row)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(12)
        if who == "user":
            l.addStretch(1)
            w.setMaximumWidth(max(240, int(self.width() * 0.82)))
            l.addWidget(w)
        elif who == "ai":
            l.addWidget(Avatar(26), 0, Qt.AlignmentFlag.AlignTop)
            l.addWidget(w, 1)
        else:
            l.addSpacing(38)
            l.addWidget(w, 1)
        self.vbox.insertWidget(self.vbox.count() - 1, row)
        self._follow = True
        from .motion import fade_in
        fade_in(row, ms=340, dy=12)
        return row

    def add_item(self, role: str, kind: str, text: str, data: dict | None = None, interactive=False):
        if kind == "proposal" and data:
            self._row(self._proposal_card(data, interactive), "ai")
        elif kind == "results" and data:
            self._row(self._results_card(data), "ai")
        elif kind == "error":
            f, l = _block("errorMsg", (14, 11, 14, 12))
            l.addWidget(_text(text))
            self._row(f, "ai")
        elif role == "user":
            f, l = _block("userMsg", (14, 10, 14, 11))
            l.addWidget(_text(html.escape(text).replace("\n", "<br>"), md=False))
            self._row(f, "user")
        else:
            w = _text(text, color=None)
            w.setContentsMargins(0, 3, 0, 0)
            self._row(w, "ai" if role != "system" else "plain")

    def add_welcome(self, examples: list[str]):
        w = QWidget()
        w.setObjectName("transparent")
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 3, 0, 0)
        v.setSpacing(10)
        v.addWidget(_text("Tell me what the board should do: what it controls or measures, what powers it, and "
                          "anything it has to fit. I'll ask about what's missing, show you a proposal, then build "
                          "the KiCad schematic and PCB and check them."))
        for ex in examples:
            b = button("\n".join(textwrap.wrap(ex, 42)), "prompt")
            b.setStyleSheet("text-align:left; border-radius:12px; padding:9px 12px;")
            b.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            b.clicked.connect(lambda _=False, t=ex: (self.input.setPlainText(t), self.input.setFocus()))
            v.addWidget(b)
        self._row(w, "ai")

    # ------------------------------------------------------------------ cards
    def _proposal_card(self, p: dict, interactive: bool) -> QFrame:
        f, l = _block()
        l.addWidget(label("Proposal", "label"))
        t = _text(f"**{html.escape(p.get('title', ''))}**")
        t.setStyleSheet("background:transparent; font-size:15px;")
        l.addWidget(t)
        b, tot = p.get("board", {}), p.get("totals", {})
        facts = (f"{b.get('width_mm', 0):g} × {b.get('height_mm', 0):g} mm  ·  {b.get('layers')} layers  ·  "
                 f"{tot.get('components')} parts  ·  {tot.get('nets')} nets")
        fl = _text(facts, md=False, obj="mono")
        l.addWidget(fl)
        if p.get("overview"):
            l.addWidget(_text(html.escape(p["overview"]), md=False, color=TEXT2))

        def section(title, items):
            if not items:
                return
            l.addSpacing(4)
            l.addWidget(label(title, "label"))
            l.addWidget(_text("\n".join(f"- {i}" for i in items)))
        section("Architecture", [a for a in p.get("architecture", []) if a])
        rows = "".join(f"<tr><td style='padding:2px 10px 2px 0; color:#6B778D'>{x['qty']}×</td>"
                       f"<td style='padding:2px 12px 2px 0'>{html.escape(x['part'])}</td>"
                       f"<td style='color:#A3AEC2'>{html.escape(x['role'])}"
                       f"{' <span style=\"color:#FBBF24\">· unverified</span>' if x['status'] == 'ai_suggested' else ''}"
                       f"</td></tr>" for x in p.get("key_parts", [])[:14])
        if rows:
            l.addSpacing(4)
            l.addWidget(label("Key parts", "label"))
            l.addWidget(_text(f"<table width='100%'>{rows}</table>", md=False))
        fc = p.get("functional_checks")
        if fc is not None:
            l.addSpacing(4)
            l.addWidget(label("Design checks", "label"))
            ok_ = not fc.get("errors") and not fc.get("warnings")
            l.addWidget(_text("Supply ranges, voltage ratings, LED currents, I2C addresses and boot pins: "
                              + ("no problems found." if ok_ else
                                 f"{fc.get('errors', 0)} errors, {fc.get('warnings', 0)} warnings."),
                              md=False, color=TEXT2))
        section("Changes from the current revision", [f"`{c}`" for c in p.get("changes", [])[:12]])
        section("Assumptions to confirm", p.get("assumptions", []))
        section("Risks and limits", p.get("risks", []) + p.get("limitations", []))
        if interactive:
            l.addSpacing(6)
            ok = button("Approve and generate", "primary", "zap")
            ok.setMinimumHeight(38)
            ch = button("Request changes", "text")
            rj = button("Reject", "textDanger")
            ok.clicked.connect(self.approve)
            ch.clicked.connect(lambda: (self.request_changes.emit(), self.input.setFocus()))
            rj.clicked.connect(self.reject)
            ok.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            l.addWidget(ok)
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(ch)
            row.addStretch(1)
            row.addWidget(rj)
            l.addLayout(row)
            self._proposal_buttons = (ok, ch, rj)
        return f

    def disable_proposal_buttons(self):
        for b in getattr(self, "_proposal_buttons", ()):
            try:
                b.setEnabled(False)
            except RuntimeError:          # card already removed
                pass

    def _results_card(self, r: dict) -> QFrame:
        status = r.get("validation_status", "")
        text, _ = STATUS_LABEL.get(status, (status, ""))
        f, l = _block()
        from .motion import StepIcon
        tone = TONE_OF_STATUS.get(status, "none")
        head = QHBoxLayout()
        head.setSpacing(12)
        badge = StepIcon(size=34, state="done")
        QTimer.singleShot(250, lambda b=badge, ok=tone == "ok": b.finish(ok))
        head.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        hv = QVBoxLayout()
        hv.setSpacing(0)
        hv.addWidget(label("Result", "label"))
        st = label(text, {"ok": "statusOk", "warn": "statusWarn", "err": "statusErr"}.get(tone, "statusNone"))
        hv.addWidget(st)
        head.addLayout(hv, 1)
        l.addLayout(head)
        drc = r.get("drc") or {}
        erc = (r.get("erc") or {}).get("counts") or {}
        stats = [("routed", "yes" if r.get("routing_complete") else "no"), ("unrouted", r.get("unrouted", "–")),
                 ("DRC errors", drc.get("errors", "–")), ("ERC errors", erc.get("error", "–") if erc else "–")]
        st = "&nbsp;&nbsp;·&nbsp;&nbsp;".join(f"<span style='color:#6B778D'>{k}</span> {v}" for k, v in stats)
        l.addWidget(_text(st, md=False, obj="mono"))
        if r.get("narrowed_rails"):
            n = r["narrowed_rails"]
            l.addWidget(_text(f"{', '.join(n['nets'])} uses {n['width_mm']} mm traces in tight spots "
                              "(low-current logic rail).", md=False, color=TEXT2))
        if r.get("unrouted_connections"):
            l.addWidget(label("Unrouted connections", "label"))
            l.addWidget(_text("\n".join(f"- `{u}`" for u in r["unrouted_connections"][:8])))
        if r.get("recommendations"):
            l.addWidget(label("Your options", "label"))
            l.addWidget(_text("\n".join(f"- {x}" for x in r["recommendations"])))
        row = QHBoxLayout()
        row.setSpacing(2)
        for name, tab in (("Open 2D", "2D PCB"), ("Open 3D", "3D PCB"), ("See checks", "Validation")):
            b = button(name, "text")
            b.clicked.connect(lambda _=False, t=tab: self.open_tab.emit(t))
            row.addWidget(b)
        row.addStretch(1)
        l.addLayout(row)
        return f

    # --------------------------------------------------------------- progress
    def start_progress(self):
        f, l = _block()
        top = QHBoxLayout()
        top.addWidget(label("Building with KiCad", "label"))
        top.addStretch(1)
        cancel = button("Cancel", "textDanger")
        cancel.clicked.connect(self.cancel_job)
        self._cancel_btn = cancel
        top.addWidget(cancel)
        l.addLayout(top)
        from .motion import FlowBar
        self._bar = FlowBar()
        l.addWidget(self._bar)
        self._steps_box = QVBoxLayout()
        self._steps_box.setSpacing(4)
        l.addLayout(self._steps_box)
        self._step_rows: list[list] = []
        self.progress_card = self._row(f, "ai")

    def _render_row(self, row):
        name, state, lab, icon = row
        if state == "running":
            tail = ""
        elif state.startswith("ok"):
            tail = state[3:]
            icon.finish(True)
        else:
            tail = state
            icon.finish(False)
        lab.setText(f"<span style='color:#E7ECF4'>{html.escape(name)}</span>"
                    f"<span style='color:#6B778D'>&nbsp;&nbsp;{html.escape(tail)}</span>")

    def progress_event(self, e: dict):
        if not self.progress_card:
            return
        name = e.get("name", "")
        base = name.split("#")[0]
        label_ = STEP_NAMES.get(base, base) + (f" ({name.split('#')[1]})" if "#" in name else "")
        if e["type"] == "step_start":
            from .motion import StepIcon, fade_in
            line = QWidget()
            line.setObjectName("transparent")
            hl = QHBoxLayout(line)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(9)
            icon = StepIcon()
            hl.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
            lab = QLabel()
            lab.setTextFormat(Qt.TextFormat.RichText)
            lab.setStyleSheet("background:transparent; font-size:12.5px;")
            hl.addWidget(lab, 1)
            row = [label_, "running", lab, icon, line]
            self._step_rows.append(row)
            self._steps_box.addWidget(line)
            fade_in(line, ms=260, dy=6)
            while len(self._step_rows) > 10:
                self._step_rows.pop(0)[4].deleteLater()
        else:
            row = next((r for r in reversed(self._step_rows) if r[0] == label_ and r[1] == "running"), None)
            if row is None:
                return
            row[1] = f"{e.get('status')} {e.get('seconds', 0):.0f}s"
        self._render_row(row[:4])

    def end_progress(self):
        if self.progress_card:
            self._bar.finish()
            self._cancel_btn.setEnabled(False)
        self.progress_card = None
