"""Workspace side panels: design summary, validation, inspector, logs."""
from __future__ import annotations

import html
import json
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout,
                               QWidget)

from ..engineering.validation.drc import parse_json_report, parse_report
from . import icons
from .chat import STATUS_LABEL
from .components import StatusLine, TONE_OF_STATUS, button, hair, label


def esc(x) -> str:
    return html.escape(str(x))


CSS = """<style>
 body { color:#E7ECF4; font-family:Inter, sans-serif; font-size:13px; line-height:155%; }
 h2 { font-size:20px; font-weight:700; margin:4px 0 10px; } h3 { color:#A3AEC2; font-size:12px; font-weight:600; margin:22px 0 8px; }
 td, th { padding:5px 16px 5px 0; vertical-align:top; } th { color:#6B778D; text-align:left; font-weight:600; }
 .muted { color:#A3AEC2; } .warn { color:#FBBF24; } .ok { color:#34D399; } .err { color:#F87171; } code { color:#67E8F9; font-family:"Roboto Mono"; }
 li { margin:3px 0; } pre { font-family:"Roboto Mono"; font-size:12px; }
</style>"""


class SummaryView(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self.setStyleSheet("QTextBrowser { background:transparent; border:none; padding:26px 34px; }")
        self.show_data(None, None)

    def show_data(self, spec: dict | None, proposal: dict | None):
        if not spec and not proposal:
            self.setHtml(CSS + "<p class='muted'>No requirements yet. Describe your board in the chat.</p>")
            return
        h = [CSS]
        if proposal:
            h.append(f"<h2>{esc(proposal.get('title', ''))}</h2><p>{esc(proposal.get('overview', ''))}</p>")
            h.append("<h3>Architecture</h3><ul>" + "".join(f"<li>{esc(a)}</li>" for a in proposal.get("architecture", []) if a) + "</ul>")
            h.append("<h3>Key parts</h3><table><tr><th>Qty</th><th>Part</th><th>Footprint</th><th>Role</th><th>Status</th></tr>" +
                     "".join(f"<tr><td>{p['qty']}</td><td><b>{esc(p['part'])}</b></td><td class='muted'>{esc(p['footprint'])}</td>"
                             f"<td>{esc(p['role'])}</td><td class='{'warn' if p['status'] == 'ai_suggested' else 'muted'}'>"
                             f"{esc(p['status'])}</td></tr>" for p in proposal.get("key_parts", [])) + "</table>")
            h.append("<h3>Selection rules</h3><ul>" + "".join(f"<li>{esc(r)}</li>" for r in proposal.get("selection_rules", [])) + "</ul>")
            h.append("<h3>Assumptions</h3><ul>" + "".join(f"<li>{esc(r)}</li>" for r in proposal.get("assumptions", [])) + "</ul>")
            h.append("<h3>Risks and limitations</h3><ul>" + "".join(
                f"<li>{esc(r)}</li>" for r in proposal.get("risks", []) + proposal.get("limitations", [])) + "</ul>")
            h.append("<h3>Part counts</h3><p class='muted'>" + ", ".join(
                f"{esc(k)} × {v}" for k, v in proposal.get("part_counts", {}).items()) + "</p>")
        if spec:
            h.append("<h3>Structured requirements</h3><pre style='color:#B8C4D9'>" + esc(json.dumps(spec, indent=2)) + "</pre>")
        self.setHtml("".join(h))


class ValidationView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(34, 26, 34, 20)
        lay.setSpacing(10)
        t = QLabel("Checks")
        t.setObjectName("display")
        lay.addWidget(t)
        self.badge = StatusLine("Not checked yet", "none")
        lay.addWidget(self.badge)
        self.info = QTextBrowser()
        self.info.setStyleSheet("QTextBrowser { background:transparent; border:none; }")
        self.info.setMaximumHeight(260)
        lay.addWidget(self.info)
        h3 = QLabel("Design rule findings, as KiCad reports them")
        h3.setObjectName("label")
        lay.addWidget(h3)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Severity", "Type", "Message", "Location / items"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        lay.addWidget(self.table, 1)
        self.show_revision(None)

    def show_revision(self, rev):
        self.table.setRowCount(0)
        if rev is None:
            self.badge.set("Not checked yet", "none")
            self.info.setHtml(CSS + "<p class='muted'>Nothing generated yet. Validation runs automatically after generation.</p>")
            return
        status = rev.status or "validation_unavailable"
        label_, _ = STATUS_LABEL.get(status, (status, ""))
        self.badge.set(label_, TONE_OF_STATUS.get(status, "none"))
        s = rev.summary or {}
        drc = s.get("drc") or {}
        h = [CSS, f"<p><b>Revision {rev.rev}</b> · {esc(time.strftime('%Y-%m-%d %H:%M', time.localtime(rev.ts)))} · "
                  f"{esc(rev.description)}</p>"]
        if s.get("error"):
            h.append(f"<p class='err'>{esc(s['error'])}</p>")
        else:
            h.append(f"<table><tr><th>Routing</th><td>{'complete' if s.get('routing_complete') else '<span class=err>incomplete</span>'}"
                     f" · unrouted {s.get('unrouted')} · {s.get('tracks')} track segments · {s.get('vias')} vias</td></tr>"
                     f"<tr><th>DRC</th><td>{drc.get('errors')} errors · {drc.get('warnings')} warnings · "
                     f"{drc.get('unconnected')} unconnected · {drc.get('footprint_errors')} footprint errors</td></tr>"
                     f"<tr><th>Checks run</th><td>{esc(' · '.join(s.get('validation_scope', [])))}</td></tr></table>")
            if s.get("unrouted_connections"):
                h.append("<h3>Unrouted connections</h3><ul>" + "".join(f"<li><code>{esc(u)}</code></li>" for u in s["unrouted_connections"]) + "</ul>")
            if s.get("recommendations"):
                h.append("<h3>Options (need your decision)</h3><ul>" + "".join(f"<li>{esc(r)}</li>" for r in s["recommendations"]) + "</ul>")
            reps = s.get("repairs") or []
            if reps:
                h.append("<h3>Automatic repairs applied</h3><ul>")
                for r in reps:
                    if r.get("kind") == "targeted":
                        for op in r.get("ops", []):
                            tgt = op.get("refs") or op.get("pads") or f"{op.get('kind', '')} at {op.get('x', '')},{op.get('y', '')}"
                            n = len(tgt) if isinstance(tgt, list) else 1
                            h.append(f"<li>{esc(op['op'])}: {n} item(s)</li>")
                    elif r.get("kind") == "relayout":
                        h.append(f"<li>re-layout ({esc(', '.join(r.get('trigger', [])))}): {esc(r.get('action') or r.get('reason', ''))}</li>")
                h.append("</ul>")
        self.info.setHtml("".join(h))
        rdir = rev.dir / "reports"
        reports = sorted([*rdir.glob("drc#*.rpt"), *rdir.glob("drc#*.json")],
                         key=lambda p: int(p.stem.split("#")[1])) if rdir.exists() else []
        erc = (rev.summary or {}).get("erc")
        if erc:
            c = erc.get("counts", {})
            types = ", ".join(f"{k} × {v}" for k, v in (erc.get("types") or {}).items()) or "none"
            self.info.append(f"<p><b>ERC</b> {c.get('error', 0)} errors · {c.get('warning', 0)} warnings "
                             f"<span class='muted'>({esc(types)})</span></p>")
            for w in erc.get("waived") or []:
                who = ", ".join(i.split(" [")[0].replace("Symbol ", "") for i in w.get("items", [])[:2])
                self.info.append(f"<p class='muted'>ERC waived · {esc(who)}: {esc(w.get('reason', ''))}</p>")
        tw =(rev.summary or {}).get("trace_widths") or {}
        if tw:
            bad = tw.get("below_required") or {}
            if bad:
                self.info.append("<p><b>Trace widths</b> <span class='err'>under the required width:</span> " +
                                 ", ".join(f"{esc(n)} {v['narrowest']} mm (needs {v['required']} mm)"
                                           for n, v in bad.items()) + "</p>")
            else:
                rm = tw.get("reduced_margin") or {}
                if rm:
                    self.info.append("<p><b>Trace widths</b> <span class='warn'>reduced current margin:</span> " +
                                     ", ".join(f"{esc(n)} {v['narrowest']} mm (planned {v['required']} mm with 1.5x "
                                               "margin; still wide enough for the actual current)"
                                               for n, v in rm.items()) + "</p>")
                necks = ", ".join(f"{esc(n)} {w} mm" for n, w in (tw.get("pin_neck_downs") or {}).items())
                self.info.append("<p><b>Trace widths</b> <span class='ok'>every current path at its required "
                                 "width</span>" + (f" <span class='muted'>(neck-downs into fine pins: {necks})</span>"
                                                    if necks else "") + "</p>")
        dec = (rev.summary or {}).get("decoupling") or []
        if dec:
            rows = "".join(f"<tr><td>{esc(d['cap'])} → {esc(d['chip'])}</td><td>{esc(d['net'])}</td>"
                           f"<td style='font-family:Roboto Mono'>{d['distance_mm']:.1f} mm</td>"
                           f"<td class='{ {'ok': 'ok', 'acceptable': 'muted', 'far': 'warn'}[d['status']] }'>"
                           f"{ {'ok': 'close', 'acceptable': 'acceptable', 'far': 'too far'}[d['status']] }</td></tr>"
                           for d in dec)
            self.info.append(f"<p><b>Decoupling and regulator loops</b> <span class='muted'>(distance from each "
                             f"capacitor or inductor to the chip pin it serves)</span></p><table>{rows}</table>")
        inc = (rev.summary or {}).get("incremental") or {}
        if inc:
            self.info.append(f"<p><b>Changes</b> <span class='muted'>against revision {inc.get('base_revision')}: "
                             f"{inc.get('kept_refs', 0)} parts kept their designators; "
                             f"{len(inc.get('kept_blocks') or [])} unchanged blocks kept their place"
                             "</span></p>")
        fn = (rev.summary or {}).get("functional") or {}
        if fn.get("findings") is not None:
            items = fn["findings"]
            head = ("<span class='ok'>no problems found</span>" if not items else
                    f"<span class='{'err' if fn.get('errors') else 'warn'}'>{fn.get('errors', 0)} errors, "
                    f"{fn.get('warnings', 0)} warnings</span>")
            self.info.append("<p><b>Design checks</b> <span class='muted'>(datasheet limits, supply rails, LED "
                             "currents, I2C addresses, ESP32 boot pins)</span> " + head +
                             "".join(f"<br><span class='{ {'error': 'err', 'warning': 'warn'}.get(f['severity'], 'muted') }'>"
                                     f"{esc(f['message'])}</span>" for f in items) + "</p>")
        op = (rev.summary or {}).get("order_package") or {}
        if op.get("error"):
            self.info.append(f"<p><b>Order package</b> <span class='err'>not built: {esc(op['error'])}</span></p>")
        elif op:
            ns = op.get("needs_sourcing") or []
            notes = op.get("capability_notes") or []
            self.info.append("<p><b>Order package</b> <span class='ok'>Gerber zip, BOM and pick-and-place ready</span>"
                             + (f" <span class='warn'>· {len(ns)} BOM lines need a supplier part number</span>" if ns else "")
                             + "".join(f"<br><span class='warn'>{esc(n)}</span>" for n in notes) + "</p>")
        if reports:
            res = parse_json_report(reports[-1]) if reports[-1].suffix == ".json" else parse_report(reports[-1])
            for i in res.issues:
                r = self.table.rowCount()
                self.table.insertRow(r)
                for col, val in enumerate((i.severity, i.kind, i.message.split(" (Rule")[0], "\n".join(i.items[:3]))):
                    it = QTableWidgetItem(val)
                    if col == 0:
                        it.setForeground(QColor("#F87171") if val == "error" else QColor("#FBBF24"))
                    self.table.setItem(r, col, it)
            if not res.issues:
                self.table.insertRow(0)
                it = QTableWidgetItem("none")
                it.setForeground(QColor("#4ADE80"))
                self.table.setItem(0, 0, it)
                self.table.setItem(0, 2, QTableWidgetItem(f"KiCad DRC reported no issues ({reports[-1].name})"))
            self.table.resizeRowsToContents()


class InspectorPanel(QWidget):
    """Right pane: state, specs, the one export action and the other files, part, revisions."""
    rollback = Signal(int)
    export = Signal(str)
    open_in_kicad = Signal()
    show_ref = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pane")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QWidget()
        head.setObjectName("transparent")
        head.setFixedHeight(52)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(20, 0, 14, 0)
        hl.addWidget(label("Board", "title"))
        hl.addStretch(1)
        outer.addWidget(head)
        outer.addWidget(hair())
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.viewport().setAutoFillBackground(False)
        inner = QWidget()
        inner.setObjectName("scrollInner")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(20, 18, 20, 20)
        lay.setSpacing(6)
        sc.setWidget(inner)
        outer.addWidget(sc, 1)

        self.state = StatusLine("Nothing generated yet", "none")
        lay.addWidget(self.state)
        self.state_sub = label("", "dim", True)
        lay.addWidget(self.state_sub)
        lay.addSpacing(14)

        self.spec_grid = QVBoxLayout()
        self.spec_grid.setSpacing(0)
        lay.addLayout(self.spec_grid)
        lay.addSpacing(18)

        self.export_main = button("Export fabrication files", "primary", "download")
        self.export_main.setMinimumHeight(40)
        self.export_main.setToolTip("Order package: Gerber .zip, BOM and pick-and-place for JLCPCB / PCBWay,\n"
                                    "and a README with the exact values for the order form")
        self.export_main.clicked.connect(lambda: self.export.emit("fab"))
        lay.addWidget(self.export_main)
        lay.addSpacing(6)
        self.export_btns = [self.export_main]
        for key, text, ext, ic in (("bom", "Bill of materials", ".csv", "list-checks"),
                                   ("pos", "Pick and place", ".csv", "crosshair"),
                                   ("step", "3D model", ".step", "box"),
                                   ("pdf", "Schematic", ".pdf", "file-text"),
                                   ("native", "KiCad project", ".zip", "folder")):
            b = self._file_row(text, ext, ic)
            b.clicked.connect(lambda _=False, k=key: self.export.emit(k))
            lay.addWidget(b)
            self.export_btns.append(b)
        self.kicad_btn = self._file_row("Open in KiCad", "", "external-link")
        self.kicad_btn.clicked.connect(self.open_in_kicad)
        lay.addWidget(self.kicad_btn)

        lay.addSpacing(18)
        lay.addWidget(label("Selected part", "label"))
        self.comp = QLabel("<span style='color:#6B778D'>Click a part in the 2D view.</span>")
        self.comp.setTextFormat(Qt.TextFormat.RichText)
        self.comp.setWordWrap(True)
        self.comp.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.comp)

        lay.addSpacing(18)
        rh = QHBoxLayout()
        rh.addWidget(label("Revisions", "label"))
        rh.addStretch(1)
        self.rb = button("Restore", "text")
        self.rb.setToolTip("Make the selected revision current")
        self.rb.clicked.connect(self._rollback)
        rh.addWidget(self.rb)
        lay.addLayout(rh)
        self.revs = QListWidget()
        self.revs.setMinimumHeight(140)
        lay.addWidget(self.revs)
        lay.addStretch(1)
        self.board = None
        self._set_specs([])
        self.set_enabled_outputs(False)

    @staticmethod
    def _file_row(text, ext, ic) -> QPushButton:
        b = QPushButton()
        b.setObjectName("row")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        h = QHBoxLayout(b)
        h.setContentsMargins(10, 0, 10, 0)
        h.setSpacing(10)
        icl = QLabel()
        icl.setPixmap(icons.pixmap(ic, "#A3AEC2", 16))
        t = label(text)
        e = label(ext, "dim")
        for w in (icl, t, e):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            w.setStyleSheet("background:transparent;" + (" color:#6B778D;" if w is e else ""))
        h.addWidget(icl)
        h.addWidget(t, 1)
        h.addWidget(e)
        b.setMinimumHeight(38)
        b._parts = (icl, t, e, ic)
        return b

    def _set_specs(self, rows):
        while self.spec_grid.count():
            it = self.spec_grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for i, (k, v) in enumerate(rows):
            if i:
                self.spec_grid.addWidget(hair())
            w = QWidget()
            w.setObjectName("transparent")
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 8, 0, 8)
            h.addWidget(label(k, "specKey"))
            h.addStretch(1)
            val = label("", "specVal")
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            h.addWidget(val)
            self.spec_grid.addWidget(w)
            from .motion import count_up, fade_in
            count_up(val, str(v), ms=600 + 60 * i)
            fade_in(w, ms=300, delay=40 * i, dy=4)

    def set_enabled_outputs(self, on: bool):
        for b in self.export_btns + [self.kicad_btn]:
            b.setEnabled(on)
            parts = getattr(b, "_parts", None)
            if parts:
                icl, t, e, ic = parts
                icl.setPixmap(icons.pixmap(ic, "#A3AEC2" if on else "#3A4763", 16))
                t.setStyleSheet("background:transparent;" + ("" if on else " color:#4B5670;"))

    def show_project(self, rev, revisions, current):
        self.revs.clear()
        self.board = None
        self.show_component("")
        for r in reversed(revisions):
            label_, _ = STATUS_LABEL.get(r.status or "", (r.status or "", ""))
            it = QListWidgetItem(f"r{r.rev}{'  · current' if r.rev == current else ''}   {r.description[:48]}")
            it.setToolTip(label_)
            it.setData(Qt.ItemDataRole.UserRole, r.rev)
            self.revs.addItem(it)
        self.board = None
        if rev is None:
            self.state.set("Nothing generated yet", "none")
            self.state_sub.setText("Approve a proposal in the conversation to build the board.")
            self._set_specs([])
            self.set_enabled_outputs(False)
            return
        bj = rev.dir / "renders" / "layers" / "board.json"
        if bj.exists():
            self.board = json.loads(bj.read_text(encoding="utf-8"))
        s = rev.summary or {}
        prop = json.loads((rev.dir / "proposal.json").read_text(encoding="utf-8")) \
            if (rev.dir / "proposal.json").exists() else {}
        b = prop.get("board", {})
        drc = s.get("drc") or {}
        erc = (s.get("erc") or {}).get("counts") or {}
        label_, _ = STATUS_LABEL.get(rev.status or "", (rev.status or "", ""))
        self.state.set(label_, TONE_OF_STATUS.get(rev.status or "", "none"))
        sub = []
        if drc:
            sub.append(f"DRC {drc.get('errors', 0)} errors, {drc.get('warnings', 0)} warnings")
        if erc:
            sub.append(f"ERC {erc.get('error', 0)} errors")
        if s.get("unrouted"):
            sub.append(f"{s.get('unrouted')} unrouted")
        self.state_sub.setText(" · ".join(sub))
        self._set_specs([("Size", f"{b.get('width_mm', 0):g} × {b.get('height_mm', 0):g} mm" if b else "–"),
                         ("Layers", b.get("layers", "–")),
                         ("Parts", prop.get("totals", {}).get("components", "–")),
                         ("Nets", prop.get("totals", {}).get("nets", "–")),
                         ("Tracks / vias", f"{s.get('tracks', '–')} / {s.get('vias', '–')}"),
                         ("Revision", f"r{rev.rev}")])
        self.set_enabled_outputs(s.get("error") is None and (rev.dir / "fab").exists())

    def show_component(self, ref: str):
        if not ref or not self.board:
            self.comp.setText("<span style='color:#6B778D'>Click a part in the 2D view.</span>")
            return
        f = next((x for x in self.board["footprints"] if x["ref"] == ref), None)
        if not f:
            return
        desc = self.board.get("descriptions", {}).get(ref, "")
        pads = "".join(f"<tr><td style='color:#6B778D; padding-right:12px'>{esc(p['num'])}</td>"
                       f"<td style='font-family:Roboto Mono; color:#A3AEC2'>{esc(p['net'] or '–')}</td></tr>"
                       for p in f["pad_geom"][:24])
        self.comp.setText(f"<span style='font-size:15px; font-weight:600'>{esc(ref)}</span>"
                          f"&nbsp;&nbsp;<span style='color:#A3AEC2'>{esc(f['value'])}</span><br>"
                          f"<span style='color:#A3AEC2'>{esc(desc)}</span><br>"
                          f"<span style='color:#6B778D; font-family:Roboto Mono; font-size:12px'>{esc(f['fpid'])}</span>"
                          f"<table style='margin-top:8px'>{pads}</table>")

    def _rollback(self):
        it = self.revs.currentItem()
        if it:
            self.rollback.emit(int(it.data(Qt.ItemDataRole.UserRole)))


class LogPanel(QFrame):
    line = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pane")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 10)
        head = QHBoxLayout()
        t = QLabel("Engineering log")
        t.setObjectName("label")
        head.addWidget(t)
        head.addStretch(1)
        self.open_btn = QPushButton("Open log folder")
        self.open_btn.setObjectName("text")
        head.addWidget(self.open_btn)
        lay.addLayout(head)
        self.text = QPlainTextEdit()
        self.text.setObjectName("mono")
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(3000)
        self.text.setStyleSheet("background:transparent; border:none; color:#A3AEC2;")
        lay.addWidget(self.text)
        self.line.connect(self.text.appendPlainText)

    def attach(self, ring):
        for l in list(ring.records)[-400:]:
            self.text.appendPlainText(l)
        ring.listeners.append(self.line.emit)      # thread-safe via queued signal
