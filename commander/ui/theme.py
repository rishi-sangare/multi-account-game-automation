"""Design system: tokens, fonts, Qt stylesheet and a small vector icon set.

Direction: an instrument panel. Near-black graphite, hairline borders, tight spacing, tabular
monospace numbers, colour reserved for state. One warm accent (brass) for focus/selection, red only
for the exit controls.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPen, QPixmap

# --------------------------------------------------------------------------- tokens

BG = "#0B0C0E"
SURFACE = "#111316"
SURFACE_2 = "#16191D"
SURFACE_3 = "#1C2025"
HOVER = "#20252B"
BORDER = "#22262C"
BORDER_2 = "#2D333A"
TEXT = "#E6E8EB"
TEXT_2 = "#A0A7B0"
TEXT_3 = "#69717B"
ACCENT = "#D9A441"          # brass
ACCENT_DIM = "#3A2F1A"
DANGER = "#E5484D"
DANGER_HI = "#F2555A"
DANGER_DK = "#B4363A"
OK = "#4CC38A"

STATE_COLORS = {
    "STOPPED": "#4A5059",
    "LAUNCHING": "#7E8FA3",
    "CONNECTING": "#7E8FA3",
    "WATCHING": "#4F9FE0",
    "JOINING": "#E2B046",
    "IN_BATTLE": "#4CC38A",
    "EXITING": "#F0844C",
    "CLOSING": "#F0844C",
    "EXITED": "#8B939D",
    "PAUSED": "#9A8FD0",
    "ERROR": "#E5484D",
}
STATE_LABELS = {
    "STOPPED": "Stopped", "LAUNCHING": "Launching", "CONNECTING": "Connecting", "WATCHING": "Watching",
    "JOINING": "Joining", "IN_BATTLE": "In battle", "EXITING": "Exiting", "CLOSING": "Closing",
    "EXITED": "Exited", "PAUSED": "Paused", "ERROR": "Error",
}

# --------------------------------------------------------------------------- fonts

UI_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"


def init_fonts() -> None:
    global UI_FAMILY, MONO_FAMILY
    fams = set(QFontDatabase.families())
    for f in ("Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI"):
        if f in fams:
            UI_FAMILY = f
            break
    for f in ("Cascadia Mono", "Cascadia Code", "Consolas"):
        if f in fams:
            MONO_FAMILY = f
            break


def ui_font(pt: float = 9.0, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(UI_FAMILY)
    f.setPointSizeF(pt)
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def mono_font(pt: float = 9.0, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(MONO_FAMILY)
    f.setPointSizeF(pt)
    f.setWeight(weight)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


def caps_font(pt: float = 7.2) -> QFont:
    f = ui_font(pt, QFont.Weight.DemiBold)
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.1)
    f.setCapitalization(QFont.Capitalization.AllUppercase)
    return f


# --------------------------------------------------------------------------- stylesheet


def _icon_png(name: str, color: str, size: int) -> str:
    """QSS can only reference image files: render our vector icons into the temp folder once."""
    import tempfile
    from pathlib import Path
    path = Path(tempfile.gettempdir()) / f"ptc_{name}_{color.strip('#')}_{size}.png"
    if not path.exists():
        icon_pixmap(name, color, size, 2.0).save(str(path))
    return path.as_posix()


def _check_png() -> str:
    return _icon_png("check", "#17130B", 14)


def stylesheet() -> str:
    return f"""
* {{ outline: none; }}
QWidget {{ background: {BG}; color: {TEXT}; font-family: "{UI_FAMILY}"; font-size: 9pt; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER_2}; padding: 5px 7px; }}

QLabel {{ background: transparent; }}
QLabel[role="muted"] {{ color: {TEXT_2}; }}
QLabel[role="faint"] {{ color: {TEXT_3}; }}
QLabel[role="caps"] {{ color: {TEXT_3}; font-size: 7.2pt; font-weight: 600; letter-spacing: 1.1px; }}

QFrame[role="surface"] {{ background: {SURFACE}; border: none; }}
QFrame[role="hline"] {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}
QFrame[role="vline"] {{ background: {BORDER}; max-width: 1px; min-width: 1px; border: none; }}

QPushButton {{
    background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER_2}; border-radius: 4px;
    padding: 5px 11px; min-height: 18px;
}}
QPushButton:hover {{ background: {HOVER}; border-color: #3A4149; }}
QPushButton:pressed {{ background: {SURFACE_3}; }}
QPushButton:disabled {{ color: {TEXT_3}; border-color: {BORDER}; }}
QPushButton[kind="primary"] {{ background: {ACCENT}; color: #17130B; border: 1px solid {ACCENT}; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: #E5B254; border-color: #E5B254; }}
QPushButton[kind="primary"]:pressed {{ background: #C4923A; }}
QPushButton[kind="primary"]:disabled {{ background: {ACCENT_DIM}; border-color: {ACCENT_DIM}; color: #8C7448; }}
QPushButton[kind="ghost"] {{ background: transparent; border: 1px solid transparent; color: {TEXT_2}; }}
QPushButton[kind="ghost"]:hover {{ background: {SURFACE_2}; color: {TEXT}; border-color: {BORDER}; }}
QPushButton[kind="danger-outline"] {{ background: transparent; color: #F08A8D; border: 1px solid #5A2427; }}
QPushButton[kind="danger-outline"]:hover {{ background: #2A1214; border-color: #7A2E32; }}

QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 4px; padding: 3px; color: {TEXT_2}; }}
QToolButton:hover {{ background: {SURFACE_3}; border-color: {BORDER}; color: {TEXT}; }}
QToolButton:checked {{ background: {SURFACE_3}; border-color: {BORDER_2}; color: {TEXT}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {{
    background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER_2}; border-radius: 4px;
    padding: 4px 7px; selection-background-color: {ACCENT_DIM}; selection-color: {TEXT};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {TEXT_3}; background: {BG}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url("{_icon_png('chev-down', TEXT_2, 12)}"); width: 12px; height: 12px; margin-right: 6px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER_2}; selection-background-color: {HOVER};
    selection-color: {TEXT}; padding: 3px; }}

QCheckBox {{ spacing: 7px; background: transparent; color: {TEXT}; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {BORDER_2}; border-radius: 3px; background: {SURFACE}; }}
QCheckBox::indicator:hover {{ border-color: #46505A; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{_check_png()}"); }}
QRadioButton {{ spacing: 7px; background: transparent; color: {TEXT}; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid {BORDER_2}; border-radius: 8px; background: {SURFACE}; }}
QRadioButton::indicator:hover {{ border-color: #46505A; }}
QRadioButton::indicator:checked {{ border: 4px solid {ACCENT}; background: #17130B; width: 8px; height: 8px; }}

QTabWidget::pane {{ border: none; border-top: 1px solid {BORDER}; background: {BG}; top: -1px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {TEXT_3}; padding: 8px 12px 7px 12px; border: none;
    border-bottom: 2px solid transparent; font-size: 7.6pt; font-weight: 600; letter-spacing: 1px; }}
QTabBar::tab:hover {{ color: {TEXT_2}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}

QTableView {{ background: {BG}; border: none; gridline-color: transparent; selection-background-color: transparent; }}
QHeaderView {{ background: {BG}; border: none; }}
QHeaderView::section {{ background: {BG}; color: {TEXT_3}; border: none; border-bottom: 1px solid {BORDER};
    padding: 6px 8px; font-size: 7.2pt; font-weight: 600; letter-spacing: 1px; }}

QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2A2F36; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #39404A; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #2A2F36; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}

QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER_2}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 3px; color: {TEXT}; }}
QMenu::item:selected {{ background: {HOVER}; }}
QMenu::item:disabled {{ color: {TEXT_3}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}

QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {TEXT_3}; }}
QStatusBar QLabel {{ color: {TEXT_3}; font-size: 8pt; padding: 0 6px; }}
QDialog {{ background: {SURFACE}; }}
QDialog QWidget {{ background: transparent; }}
QDialog QLineEdit, QDialog QSpinBox, QDialog QDoubleSpinBox, QDialog QComboBox, QDialog QPlainTextEdit {{ background: {BG}; }}
QDialog QPushButton {{ background: {SURFACE_2}; }}
QDialog QPushButton[kind="primary"] {{ background: {ACCENT}; }}
QMessageBox {{ background: {SURFACE}; }}
"""


# --------------------------------------------------------------------------- icons


def icon(name: str, color: str = TEXT_2, size: int = 16) -> QIcon:
    return QIcon(icon_pixmap(name, color, size))


def icon_pixmap(name: str, color: str = TEXT_2, size: int = 16, dpr: float = 2.0) -> QPixmap:
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    paint_icon(p, name, QRectF(0, 0, size, size), QColor(color))
    p.end()
    return pm


def paint_icon(p: QPainter, name: str, r: QRectF, c: QColor) -> None:
    s = r.width() / 16.0
    p.save()
    p.translate(r.topLeft())
    p.scale(s, s)
    p.setPen(QPen(c, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    if name == "play":
        path.moveTo(5, 3.5); path.lineTo(12.5, 8); path.lineTo(5, 12.5); path.closeSubpath()
        p.setBrush(c); p.drawPath(path)
    elif name == "pause":
        p.setBrush(c); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(4, 3.5, 2.6, 9), 0.6, 0.6)
        p.drawRoundedRect(QRectF(9.4, 3.5, 2.6, 9), 0.6, 0.6)
    elif name == "stop":
        p.setBrush(c); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(4, 4, 8, 8), 1, 1)
    elif name == "power":
        path.arcMoveTo(QRectF(3, 3.5, 10, 10), 125); path.arcTo(QRectF(3, 3.5, 10, 10), 125, 290)
        p.drawPath(path)
        p.drawLine(QPointF(8, 2.2), QPointF(8, 7.6))
    elif name == "crosshair":
        p.drawEllipse(QRectF(3.5, 3.5, 9, 9))
        for a, b in (((8, 1.5), (8, 5)), ((8, 11), (8, 14.5)), ((1.5, 8), (5, 8)), ((11, 8), (14.5, 8))):
            p.drawLine(QPointF(*a), QPointF(*b))
    elif name == "plus":
        p.drawLine(QPointF(8, 3.5), QPointF(8, 12.5)); p.drawLine(QPointF(3.5, 8), QPointF(12.5, 8))
    elif name == "grid":
        for x, y in ((2.5, 2.5), (9, 2.5), (2.5, 9), (9, 9)):
            p.drawRoundedRect(QRectF(x, y, 4.5, 4.5), 0.8, 0.8)
    elif name == "eye":
        path.moveTo(1.8, 8); path.cubicTo(4, 3.8, 12, 3.8, 14.2, 8); path.cubicTo(12, 12.2, 4, 12.2, 1.8, 8)
        p.drawPath(path); p.drawEllipse(QPointF(8, 8), 2, 2)
    elif name == "dots":
        p.setBrush(c); p.setPen(Qt.PenStyle.NoPen)
        for x in (3.5, 8, 12.5):
            p.drawEllipse(QPointF(x, 8), 1.2, 1.2)
    elif name == "link":
        p.drawRoundedRect(QRectF(1.8, 5.5, 7, 5), 2.5, 2.5)
        p.drawRoundedRect(QRectF(7.2, 5.5, 7, 5), 2.5, 2.5)
    elif name == "refresh":
        path.arcMoveTo(QRectF(3, 3, 10, 10), 30); path.arcTo(QRectF(3, 3, 10, 10), 30, 280)
        p.drawPath(path)
        p.drawLine(QPointF(12.8, 2.6), QPointF(12.6, 5.6)); p.drawLine(QPointF(12.6, 5.6), QPointF(9.7, 5.3))
    elif name == "trash":
        p.drawLine(QPointF(3, 4.5), QPointF(13, 4.5)); p.drawLine(QPointF(6.5, 4.5), QPointF(6.5, 2.8))
        p.drawLine(QPointF(6.5, 2.8), QPointF(9.5, 2.8)); p.drawLine(QPointF(9.5, 2.8), QPointF(9.5, 4.5))
        path.moveTo(4.3, 4.5); path.lineTo(5, 13.3); path.lineTo(11, 13.3); path.lineTo(11.7, 4.5)
        p.drawPath(path)
    elif name == "bolt":
        path.moveTo(9, 1.8); path.lineTo(4, 9); path.lineTo(7.8, 9); path.lineTo(7, 14.2); path.lineTo(12, 7)
        path.lineTo(8.2, 7); path.closeSubpath()
        p.setBrush(c); p.setPen(Qt.PenStyle.NoPen); p.drawPath(path)
    elif name == "exit":
        path.moveTo(9.5, 3); path.lineTo(3.5, 3); path.lineTo(3.5, 13); path.lineTo(9.5, 13)
        p.drawPath(path)
        p.drawLine(QPointF(7, 8), QPointF(14, 8)); p.drawLine(QPointF(11.2, 5.2), QPointF(14, 8))
        p.drawLine(QPointF(11.2, 10.8), QPointF(14, 8))
    elif name == "skull":                                   # kill
        p.drawLine(QPointF(4, 4), QPointF(12, 12)); p.drawLine(QPointF(12, 4), QPointF(4, 12))
    elif name == "edit":
        path.moveTo(3, 13); path.lineTo(3.6, 10.2); path.lineTo(10.6, 3.2); path.lineTo(12.8, 5.4)
        path.lineTo(5.8, 12.4); path.closeSubpath()
        p.drawPath(path)
    elif name == "chev-down":
        p.drawLine(QPointF(4, 6), QPointF(8, 10)); p.drawLine(QPointF(8, 10), QPointF(12, 6))
    elif name == "window":
        p.drawRoundedRect(QRectF(2, 3, 12, 10), 1.5, 1.5); p.drawLine(QPointF(2, 6), QPointF(14, 6))
    elif name == "check":
        p.setPen(QPen(c, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        path.moveTo(3.5, 8.3); path.lineTo(6.6, 11.2); path.lineTo(12.5, 4.8); p.drawPath(path)
    elif name == "save":
        path.moveTo(3, 3); path.lineTo(11, 3); path.lineTo(13, 5); path.lineTo(13, 13); path.lineTo(3, 13)
        path.closeSubpath(); p.drawPath(path); p.drawRect(QRectF(5.5, 9, 5, 4))
    p.restore()


def emblem_pixmap(size: int = 22, dpr: float = 2.0) -> QPixmap:
    """App mark: a brass square with three rank chevrons."""
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(ACCENT))
    p.drawRoundedRect(QRectF(0, 0, size, size), 4, 4)
    p.setPen(QPen(QColor("#17130B"), size / 11, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap,
                  Qt.PenJoinStyle.MiterJoin))
    for i in range(3):
        y = size * (0.30 + i * 0.17)
        path = QPainterPath()
        path.moveTo(size * 0.27, y)
        path.lineTo(size * 0.5, y + size * 0.13)
        path.lineTo(size * 0.73, y)
        p.drawPath(path)
    p.end()
    return pm
