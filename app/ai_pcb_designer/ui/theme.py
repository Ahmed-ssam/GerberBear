"""Gerber Bear theme: one calm system.

Surfaces are separated by tone and hairlines, not boxes in boxes. Gold is reserved for the
one primary action on screen; teal marks focus, selection and links. One radius scale
(6 / 10 / 14), one button pair (primary, quiet), sentence-case labels, no glows or gradients.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from ..core.branding import Branding

INK = "#0A0F1B"        # canvas / deepest
PANEL = "#0F1626"      # side panes, header
RAISED = "#151E31"     # blocks inside panes (messages, inputs, rows on hover)
RAISED2 = "#1B2640"
LINE = "#1C2740"       # hairlines
LINE2 = "#2A3756"
TEXT = "#E7ECF4"
TEXT2 = "#A3AEC2"
TEXT3 = "#6B778D"
GOLD = "#FFD700"
GOLD_HOVER = "#FFE347"
TEAL = "#06B6D4"
TEAL_SOFT = "#22D3EE"
OK = "#34D399"
WARN = "#FBBF24"
ERR = "#F87171"


def rgba(hexs: str, a: float) -> str:
    c = QColor(hexs)
    return f"rgba({c.red()},{c.green()},{c.blue()},{a})"


class Backdrop(QWidget):
    """Plain window ground (kept as a widget so panes can sit on one consistent tone)."""

    def __init__(self, b: Branding = None, parent=None):
        super().__init__(parent)

    def paintEvent(self, _):
        qp = QPainter(self)
        qp.fillRect(self.rect(), QColor(INK))
        qp.end()


def stylesheet(b: Branding) -> str:
    ui, mono = b.fonts["ui"], b.fonts["mono"]
    return f"""
* {{ font-family: "{ui}", "Segoe UI Variable Text", "Segoe UI", sans-serif; font-size: 13px; color: {TEXT}; }}
QMainWindow, QDialog {{ background: {INK}; }}
QWidget#transparent, QWidget#scrollInner, QWidget#qt_scrollarea_viewport {{ background: transparent; }}
QWidget#pane {{ background: {PANEL}; }}
QWidget#canvas {{ background: {INK}; }}
QWidget#homeCanvas {{ background: transparent; }}
QWidget#header {{ background: {PANEL}; border-bottom: 1px solid {LINE}; }}
QFrame#hair {{ background: {LINE}; border: none; }}

/* ---------- type ---------- */
QLabel#brand {{ font-size: 17px; font-weight: 700; letter-spacing: -0.2px; color: {TEXT}; }}
QLabel#heroBrand {{ font-size: 46px; font-weight: 800; letter-spacing: -1.2px; color: {TEXT}; }}
QLabel#heroTag {{ font-size: 15px; font-weight: 400; color: {TEXT2}; }}
QLabel#crumb {{ font-size: 14px; font-weight: 600; color: {TEXT}; }}
QLabel#crumbDim {{ font-size: 14px; color: {TEXT3}; }}
QLabel#display {{ font-size: 26px; font-weight: 700; color: {TEXT}; }}
QLabel#title {{ font-size: 15px; font-weight: 600; color: {TEXT}; }}
QLabel#label {{ font-size: 12px; font-weight: 600; color: {TEXT2}; }}
QLabel#muted {{ color: {TEXT2}; }}
QLabel#dim {{ color: {TEXT3}; font-size: 12px; }}
QLabel#mono, QPlainTextEdit#mono {{ font-family: "{mono}", "Cascadia Mono", Consolas, monospace; font-size: 12px; color: {TEXT2}; }}
QLabel#specKey {{ color: {TEXT2}; }}
QLabel#specVal {{ font-family: "{mono}", Consolas, monospace; color: {TEXT}; }}
QLabel#statusOk {{ color: {OK}; font-weight: 600; font-size: 14px; }}
QLabel#statusWarn {{ color: {WARN}; font-weight: 600; font-size: 14px; }}
QLabel#statusErr {{ color: {ERR}; font-weight: 600; font-size: 14px; }}
QLabel#statusNone {{ color: {TEXT2}; font-weight: 600; font-size: 14px; }}
QLabel#chip {{ background: {RAISED}; color: {TEXT2}; border-radius: 6px; padding: 3px 8px; font-size: 12px; }}
QLabel#simChip {{ background: #3A2408; color: #FDBA74; border-radius: 6px; padding: 3px 8px; font-size: 11px; font-weight: 700; }}

/* ---------- buttons: one primary, one quiet, one text ---------- */
QPushButton {{ background: {RAISED}; border: 1px solid {LINE2}; border-radius: 10px; padding: 8px 14px; color: {TEXT}; }}
QPushButton:hover {{ background: {RAISED2}; border-color: #3A4A6E; }}
QPushButton:pressed {{ background: {PANEL}; padding-top: 9px; padding-bottom: 7px; }}
QPushButton:disabled {{ color: {TEXT3}; background: transparent; border-color: {LINE}; }}
QPushButton:focus {{ outline: none; border-color: {TEAL}; }}
QPushButton#primary {{ background: {GOLD}; color: #14110A; border: none; font-weight: 700; padding: 9px 16px; }}
QPushButton#primary:hover {{ background: {GOLD_HOVER}; }}
QPushButton#primary:pressed {{ background: #E6C200; padding-top: 10px; padding-bottom: 8px; }}
QPushButton#primary:disabled {{ background: {RAISED}; color: {TEXT3}; }}
QPushButton#text {{ background: transparent; border: none; color: {TEAL_SOFT}; padding: 4px 6px; }}
QPushButton#text:hover {{ color: #67E8F9; }}
QPushButton#text:disabled {{ color: {TEXT3}; }}
QPushButton#textDanger {{ background: transparent; border: none; color: {TEXT2}; padding: 4px 6px; }}
QPushButton#textDanger:hover {{ color: {ERR}; }}
QPushButton#row {{ background: transparent; border: none; border-radius: 8px; padding: 9px 10px; text-align: left; color: {TEXT}; }}
QPushButton#row:hover {{ background: {RAISED}; }}
QPushButton#row:disabled {{ color: {TEXT3}; }}
QPushButton#prompt {{ background: transparent; border: 1px solid {LINE2}; border-radius: 16px; padding: 7px 14px; color: {TEXT2}; }}
QPushButton#prompt:hover {{ border-color: {TEAL}; color: {TEXT}; }}
QPushButton#seg {{ background: transparent; border: none; border-radius: 8px; padding: 6px 12px; color: {TEXT2}; font-weight: 500; }}
QPushButton#seg:hover {{ color: {TEXT}; }}
QPushButton#seg:checked {{ background: transparent; color: {TEXT}; }}
QPushButton#send {{ background: {TEAL}; border: none; border-radius: 10px; padding: 0; }}
QPushButton#send:hover {{ background: {TEAL_SOFT}; }}
QPushButton#send:disabled {{ background: {RAISED2}; }}
QToolButton#icon {{ background: transparent; border: none; border-radius: 8px; padding: 6px; }}
QToolButton#icon:hover {{ background: {RAISED}; }}
QToolButton#icon:checked {{ background: {RAISED2}; }}
QToolButton#icon::menu-indicator {{ image: none; }}

/* ---------- blocks ---------- */
QFrame#segGroup {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 10px; }}
QFrame#block {{ background: {RAISED}; border: none; border-radius: 14px; }}
QFrame#userMsg {{ background: {RAISED2}; border: none; border-radius: 14px; border-bottom-right-radius: 4px; }}
QFrame#errorMsg {{ background: {rgba(ERR, 0.08)}; border: 1px solid {rgba(ERR, 0.35)}; border-radius: 12px; }}
QFrame#composer {{ background: {RAISED}; border: 1px solid {LINE2}; border-radius: 14px; }}
QFrame#composer[focus="true"] {{ border-color: {TEAL}; }}
QFrame#composer QPlainTextEdit {{ background: transparent; border: none; padding: 2px 0; font-size: 13.5px; }}
QFrame#tile {{ background: {rgba(PANEL, 0.86)}; border: 1px solid {LINE}; border-radius: 14px; }}
QFrame#tile:hover {{ border-color: {rgba(TEAL, 0.55)}; background: {rgba(RAISED, 0.92)}; }}
QLabel#tileIcon {{ background: {rgba(GOLD, 0.09)}; border: 1px solid {rgba(GOLD, 0.25)}; border-radius: 10px; }}
QLabel#stepIcon {{ background: {rgba(TEAL, 0.08)}; border: 1px solid {rgba(TEAL, 0.35)}; border-radius: 22px; }}
QLabel#tileTitle {{ font-size: 14px; font-weight: 600; color: {TEXT}; background: transparent; }}
QLabel#tileSub {{ font-size: 12.5px; color: {TEXT2}; background: transparent; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 14px; }}
QFrame#card:hover {{ border-color: {LINE2}; }}
QFrame#thumb {{ background: {INK}; border: none; border-top-left-radius: 13px; border-top-right-radius: 13px; }}
QLabel#avatar {{ background: {INK}; border: 1px solid {LINE2}; border-radius: 13px; }}

/* ---------- inputs ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
    background: {RAISED}; border: 1px solid {LINE2}; border-radius: 10px; padding: 7px 10px;
    selection-background-color: {rgba(TEAL, 0.4)}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {{ border-color: {TEAL}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {RAISED}; border: 1px solid {LINE2}; border-radius: 8px;
    selection-background-color: {rgba(TEAL, 0.22)}; padding: 4px; }}

/* ---------- tabs (settings dialog) ---------- */
QTabWidget::pane {{ border: 1px solid {LINE}; border-radius: 12px; top: -1px; background: {PANEL}; }}
QTabBar::tab {{ background: transparent; color: {TEXT2}; padding: 9px 14px; border: none; border-radius: 8px; margin: 4px 2px; }}
QTabBar::tab:selected {{ color: {TEXT}; background: {RAISED2}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}

/* ---------- lists, tables, scroll ---------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: {LINE2}; border-radius: 3px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: #3A4A6E; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; width: 0; background: none; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px 4px; }}
QScrollBar::handle:horizontal {{ background: {LINE2}; border-radius: 3px; min-width: 36px; }}
QSplitter::handle {{ background: {LINE}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QListWidget, QTreeWidget, QTableWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ padding: 8px 8px; border-radius: 8px; margin: 1px 0; color: {TEXT}; }}
QListWidget::item:selected {{ background: {RAISED2}; }}
QListWidget::item:hover:!selected {{ background: {RAISED}; }}
QTableWidget {{ gridline-color: {LINE}; alternate-background-color: {rgba(RAISED, 0.5)}; }}
QTableWidget::item {{ padding: 6px; }}
QTableWidget::item:selected {{ background: {rgba(TEAL, 0.16)}; }}
QHeaderView::section {{ background: transparent; color: {TEXT3}; border: none; border-bottom: 1px solid {LINE};
    padding: 8px 6px; font-weight: 600; font-size: 12px; }}
QTextBrowser {{ background: transparent; border: none; }}
QProgressBar {{ background: {LINE}; border: none; border-radius: 1px; max-height: 3px; min-height: 3px; }}
QProgressBar::chunk {{ background: {TEAL}; border-radius: 1px; }}

/* ---------- status bar, menus, tooltips ---------- */
QStatusBar {{ background: {PANEL}; border-top: 1px solid {LINE}; min-height: 26px; }}
QStatusBar QLabel {{ color: {TEXT3}; font-size: 12px; padding: 0 8px; }}
QStatusBar::item {{ border: none; }}
QToolTip {{ background: {RAISED2}; color: {TEXT}; border: 1px solid {LINE2}; border-radius: 6px; padding: 6px 8px; }}
QMenu {{ background: {RAISED}; border: 1px solid {LINE2}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 8px 22px 8px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {RAISED2}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 6px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {LINE2}; border-radius: 4px; background: {RAISED}; }}
QCheckBox::indicator:checked {{ background: {TEAL}; border-color: {TEAL}; }}
QGroupBox {{ border: 1px solid {LINE}; border-radius: 12px; margin-top: 16px; padding: 14px 12px 10px 12px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; color: {TEXT2}; }}
QDialogButtonBox QPushButton {{ min-width: 84px; }}
"""
