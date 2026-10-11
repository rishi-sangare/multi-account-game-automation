"""Small custom-painted widgets used across the window."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from . import theme as T


def hline() -> QFrame:
    f = QFrame()
    f.setProperty("role", "hline")
    return f


def vline() -> QFrame:
    f = QFrame()
    f.setProperty("role", "vline")
    return f


def caps_label(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setFont(T.caps_font())
    lb.setStyleSheet(f"color: {T.TEXT_3};")
    return lb


def paint_state_pill(p: QPainter, r: QRectF, state: str, suffix: str = "") -> float:
    """Dot + label in a quiet rounded pill. Returns the pill width."""
    color = QColor(T.STATE_COLORS.get(state, T.TEXT_3))
    label = T.STATE_LABELS.get(state, state.title())
    f = T.ui_font(8.4, QFont.Weight.DemiBold)
    fm = QFontMetricsF(f)
    fs = T.mono_font(7.8)
    fms = QFontMetricsF(fs)
    w = 10 + 7 + 6 + fm.horizontalAdvance(label) + (6 + fms.horizontalAdvance(suffix) if suffix else 0) + 10
    h = 22
    pill = QRectF(r.left(), r.center().y() - h / 2, w, h)
    bg = QColor(color)
    bg.setAlpha(26)
    border = QColor(color)
    border.setAlpha(70)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(border, 1))
    p.setBrush(bg)
    p.drawRoundedRect(pill.adjusted(0.5, 0.5, -0.5, -0.5), h / 2, h / 2)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(QRectF(pill.left() + 10, pill.center().y() - 3.5, 7, 7))
    p.setFont(f)
    p.setPen(QColor(T.TEXT))
    x = pill.left() + 10 + 7 + 6
    p.drawText(QRectF(x, pill.top(), w, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
    if suffix:
        x += fm.horizontalAdvance(label) + 6
        p.setFont(fs)
        p.setPen(QColor(T.TEXT_3))
        p.drawText(QRectF(x, pill.top(), w, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, suffix)
    p.restore()
    return w


class StatePill(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "STOPPED"
        self.suffix = ""
        self.setMinimumHeight(24)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

    def set(self, state: str, suffix: str = "") -> None:
        if (state, suffix) != (self.state, self.suffix):
            self.state, self.suffix = state, suffix
            self.update()

    def sizeHint(self) -> QSize:
        return QSize(150, 24)

    def paintEvent(self, _):
        p = QPainter(self)
        paint_state_pill(p, QRectF(self.rect()), self.state, self.suffix)


class Chip(QWidget):
    """Header status chip: coloured dot, caps label, mono value."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.label = label
        self.value = "—"
        self.color = QColor(T.TEXT_3)
        self.setFixedHeight(26)

    def set(self, value: str, color: str) -> None:
        if value != self.value or QColor(color) != self.color:
            self.value, self.color = value, QColor(color)
            w = self.sizeHint().width()
            if w != self.minimumWidth():
                self.setFixedWidth(w)
            self.update()

    def _label_w(self) -> float:
        return QFontMetricsF(T.caps_font(7.0)).horizontalAdvance(self.label.upper())

    def sizeHint(self) -> QSize:
        fv = QFontMetricsF(T.mono_font(8.2))
        return QSize(int(26 + self._label_w() + 8 + fv.horizontalAdvance(self.value) + 11), 26)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(T.BORDER_2), 1))
        p.setBrush(QColor(T.SURFACE_2))
        p.drawRoundedRect(r, 4, 4)
        p.setPen(Qt.PenStyle.NoPen)
        glow = QColor(self.color)
        glow.setAlpha(60)
        p.setBrush(glow)
        p.drawEllipse(QRectF(r.left() + 9, r.center().y() - 5, 10, 10))
        p.setBrush(self.color)
        p.drawEllipse(QRectF(r.left() + 11, r.center().y() - 3, 6, 6))
        x = r.left() + 26
        p.setFont(T.caps_font(7.0))
        p.setPen(QColor(T.TEXT_3))
        lw = self._label_w()
        p.drawText(QRectF(x, r.top(), lw + 4, r.height()), Qt.AlignmentFlag.AlignVCenter, self.label)
        p.setFont(T.mono_font(8.2))
        p.setPen(QColor(T.TEXT))
        p.drawText(QRectF(x + lw + 8, r.top(), r.width(), r.height()), Qt.AlignmentFlag.AlignVCenter, self.value)


class StatTile(QWidget):
    """Caps label over a large tabular number, with an optional small unit and footnote."""

    def __init__(self, label: str, unit: str = "", parent=None):
        super().__init__(parent)
        self.label, self.unit = label, unit
        self.value, self.note = "—", ""
        self.value_color = QColor(T.TEXT)
        self.setMinimumHeight(76)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set(self, value: str, note: str = "", color: str | None = None) -> None:
        c = QColor(color or T.TEXT)
        if (value, note, c) != (self.value, self.note, self.value_color):
            self.value, self.note, self.value_color = value, note, c
            self.update()

    def sizeHint(self) -> QSize:
        return QSize(110, 76)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(16, 11, -10, -9)
        p.setFont(T.caps_font(7.0))
        p.setPen(QColor(T.TEXT_3))
        p.drawText(QRectF(r.left(), r.top(), r.width(), 14), Qt.AlignmentFlag.AlignLeft, self.label)
        fv = T.mono_font(15.0, QFont.Weight.Medium)
        p.setFont(fv)
        p.setPen(self.value_color)
        fm = QFontMetricsF(fv)
        vr = QRectF(r.left(), r.top() + 16, r.width(), 26)
        p.drawText(vr, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.value)
        x = r.left() + fm.horizontalAdvance(self.value) + 4
        if self.unit and self.value not in ("—", ""):
            p.setFont(T.ui_font(8))
            p.setPen(QColor(T.TEXT_3))
            p.drawText(QRectF(x, vr.top() + 4, 60, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.unit)
        if self.note:
            p.setFont(T.ui_font(7.6))
            p.setPen(QColor(T.TEXT_3))
            p.drawText(QRectF(r.left(), r.bottom() - 13, r.width(), 14), Qt.AlignmentFlag.AlignLeft,
                       QFontMetricsF(p.font()).elidedText(self.note, Qt.TextElideMode.ElideRight, r.width()))


class Lamp(QWidget):
    def __init__(self, parent=None, size: int = 10):
        super().__init__(parent)
        self.on = False
        self.known = False
        self.setFixedSize(size + 6, size + 6)

    def set(self, on: bool | None) -> None:
        known = on is not None
        on = bool(on)
        if (on, known) != (self.on, self.known):
            self.on, self.known = on, known
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        if not self.known:
            p.setPen(QPen(QColor(T.BORDER_2), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
        elif self.on:
            glow = QColor(T.OK)
            glow.setAlpha(55)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(r.adjusted(-3, -3, 3, 3))
            p.setBrush(QColor(T.OK))
        else:
            p.setPen(QPen(QColor("#3A4048"), 1.2))
            p.setBrush(QColor("#1A1E23"))
        p.drawEllipse(r)


class ExitAllButton(QPushButton):
    """The one big red control. Label + hotkey caption, flat, unmistakable."""

    def __init__(self, hotkey: str, parent=None):
        super().__init__(parent)
        self.hotkey = hotkey
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(40)
        self.setMinimumWidth(156)
        self.setToolTip("Leave battle on every account now")
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        base = QColor(T.DANGER_DK if self.isDown() else (T.DANGER_HI if self._hover else T.DANGER))
        p.setPen(QPen(QColor("#FF7A7E") if self._hover else QColor("#C9454A"), 1))
        p.setBrush(base)
        p.drawRoundedRect(r, 5, 5)
        T.paint_icon(p, "power", QRectF(r.left() + 12, r.center().y() - 8, 16, 16), QColor("#FFFFFF"))
        f = T.ui_font(9.6, QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        p.setFont(f)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(QRectF(r.left() + 36, r.top() + 4, r.width(), 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "EXIT ALL")
        p.setFont(T.mono_font(7.2))
        p.setPen(QColor(255, 255, 255, 170))
        p.drawText(QRectF(r.left() + 36, r.top() + 20, r.width(), 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self.hotkey)


class Segmented(QWidget):
    """Compact segmented control."""
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        n = len(options)
        for i, (key, text) in enumerate(options):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            radius = "border-top-left-radius:4px;border-bottom-left-radius:4px;" if i == 0 else ""
            radius += "border-top-right-radius:4px;border-bottom-right-radius:4px;" if i == n - 1 else ""
            b.setStyleSheet(
                f"QPushButton{{background:{T.SURFACE};color:{T.TEXT_2};border:1px solid {T.BORDER_2};"
                f"border-radius:0;{radius}padding:5px 9px;{'border-left:none;' if i else ''}}}"
                f"QPushButton:hover{{color:{T.TEXT};background:{T.SURFACE_2};}}"
                f"QPushButton:checked{{background:{T.SURFACE_3};color:{T.TEXT};border-color:{T.BORDER_2};}}")
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))

    def set_value(self, key: str) -> None:
        if key in self.buttons and not self.buttons[key].isChecked():
            self.buttons[key].setChecked(True)
