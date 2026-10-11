"""Full-screen coordinate picker.

The desktop is frozen into a screenshot first, so the colour you pick is exactly what the engine will
see. Hovering a bound game window highlights it and shows client coordinates, ratios and RGB in a
magnifier. Click = pick a point. Drag = capture a template region. Esc = cancel.

All maths is done in physical pixels (win32 + mss); Qt's logical pixels are only used for drawing.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import win32api
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ..win32 import capture
from ..win32 import windows as W
from . import theme as T


@dataclass
class PickTarget:
    acc_id: str | None
    name: str
    hwnd: int
    rect: W.Rect                  # client rect, screen coords


@dataclass
class PickResult:
    target: PickTarget
    cx: int
    cy: int
    rx: float
    ry: float
    rgb: tuple[int, int, int]
    region: tuple[int, int, int, int] | None = None      # client px l,t,r,b when dragged
    region_img: np.ndarray | None = None                 # BGRA crop
    client_w: int = 0
    client_h: int = 0


class PickerOverlay(QWidget):
    picked = Signal(object)          # PickResult
    window_picked = Signal(int)      # hwnd (window mode)
    cancelled = Signal()

    ZOOM = 12
    LENS = 11                         # pixels across in the magnifier

    def __init__(self, targets: list[PickTarget], mode: str = "point", hint: str = ""):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.mode = mode
        self.hint = hint
        self.targets = targets
        self.frame, self.ox, self.oy = capture.grab_full()     # physical, BGRA
        self.frame = self.frame.copy()
        screen = QGuiApplication.primaryScreen()
        self.dpr = screen.devicePixelRatio()
        geo = screen.virtualGeometry()
        self.setGeometry(geo)
        h, w = self.frame.shape[:2]
        rgb = np.ascontiguousarray(self.frame[:, :, [2, 1, 0]])
        self._rgb = rgb
        img = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888)
        self.pix = QPixmap.fromImage(img)
        self.pix.setDevicePixelRatio(self.dpr)
        self.cur = (0, 0)
        self.press: tuple[int, int] | None = None
        self.hover: PickTarget | None = None
        self.hover_hwnd = 0

    # -- coordinate helpers
    def _phys_cursor(self) -> tuple[int, int]:
        return win32api.GetCursorPos()

    def _to_logical(self, px: float, py: float) -> QPointF:
        return QPointF((px - self.ox) / self.dpr, (py - self.oy) / self.dpr)

    def _rgb_at(self, px: int, py: int) -> tuple[int, int, int]:
        x, y = px - self.ox, py - self.oy
        h, w = self.frame.shape[:2]
        x, y = min(max(x, 0), w - 1), min(max(y, 0), h - 1)
        b, g, r = self.frame[y, x, :3]
        return int(r), int(g), int(b)

    def _target_at(self, px: int, py: int) -> PickTarget | None:
        for t in self.targets:
            if t.rect.contains(px, py):
                return t
        return None

    # -- events
    def mouseMoveEvent(self, e):
        self.cur = self._phys_cursor()
        self.hover = self._target_at(*self.cur)
        if self.mode == "window" and self.hover is None:
            self.setVisible(True)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
            self.close()
            return
        self.press = self._phys_cursor()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self.press is None:
            return
        end = self._phys_cursor()
        start, self.press = self.press, None
        if self.mode == "window":
            self._finish_window(end)
            return
        t = self._target_at(*start)
        if t is None:
            return                                             # must pick inside a game window
        dragged = abs(end[0] - start[0]) > 6 and abs(end[1] - start[1]) > 6
        cw, ch = t.rect.w, t.rect.h
        if dragged and self.mode == "point":
            l, r = sorted((start[0], end[0]))
            tp, b = sorted((start[1], end[1]))
            l, r = max(l, t.rect.left), min(r, t.rect.right)
            tp, b = max(tp, t.rect.top), min(b, t.rect.bottom)
            crop = self.frame[tp - self.oy:b - self.oy, l - self.ox:r - self.ox].copy()
            cx, cy = (l + r) // 2 - t.rect.left, (tp + b) // 2 - t.rect.top
            res = PickResult(t, cx, cy, cx / cw, cy / ch, self._rgb_at((l + r) // 2, (tp + b) // 2),
                             region=(l - t.rect.left, tp - t.rect.top, r - t.rect.left, b - t.rect.top),
                             region_img=crop, client_w=cw, client_h=ch)
        else:
            cx, cy = start[0] - t.rect.left, start[1] - t.rect.top
            res = PickResult(t, cx, cy, round(cx / cw, 4), round(cy / ch, 4), self._rgb_at(*start),
                             client_w=cw, client_h=ch)
        self.close()
        self.picked.emit(res)

    def _finish_window(self, pt: tuple[int, int]) -> None:
        t = self._target_at(*pt)                               # targets = visible top-level windows
        self.close()
        if t:
            self.window_picked.emit(t.hwnd)
        else:
            self.cancelled.emit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()

    def showEvent(self, e):
        self.cur = self._phys_cursor()
        self.hover = self._target_at(*self.cur)
        self.activateWindow()
        self.setFocus()

    # -- painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(0, 0, self.pix)
        p.fillRect(self.rect(), QColor(0, 0, 0, 120))
        # un-dim game windows, outline the hovered one
        for t in self.targets:
            lr = QRectF(self._to_logical(t.rect.left, t.rect.top), self._to_logical(t.rect.right, t.rect.bottom))
            src = QRectF((t.rect.left - self.ox), (t.rect.top - self.oy), t.rect.w, t.rect.h)
            p.drawPixmap(lr, self.pix, src)
            hot = self.hover is t
            p.setPen(QPen(QColor(T.ACCENT) if hot else QColor(255, 255, 255, 50), 2 if hot else 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(lr.adjusted(-1, -1, 1, 1))
            self._tag(p, lr.topLeft() + QPointF(0, -22), t.name, hot)
        px, py = self.cur
        lp = self._to_logical(px, py)
        # drag rectangle
        if self.press and self.mode == "point":
            a = self._to_logical(*self.press)
            p.setPen(QPen(QColor(T.ACCENT), 1, Qt.PenStyle.DashLine))
            p.setBrush(QColor(217, 164, 65, 40))
            p.drawRect(QRectF(a, lp).normalized())
        # crosshair
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        p.drawLine(QPointF(lp.x(), 0), QPointF(lp.x(), lp.y() - 14))
        p.drawLine(QPointF(lp.x(), lp.y() + 14), QPointF(lp.x(), self.height()))
        p.drawLine(QPointF(0, lp.y()), QPointF(lp.x() - 14, lp.y()))
        p.drawLine(QPointF(lp.x() + 14, lp.y()), QPointF(self.width(), lp.y()))
        p.setPen(QPen(QColor(T.ACCENT), 1.5))
        p.drawEllipse(lp, 5, 5)
        if self.mode == "point":
            self._lens(p, px, py, lp)
        self._hintbar(p)

    def _tag(self, p: QPainter, at: QPointF, text: str, hot: bool) -> None:
        f = T.ui_font(8.4, QFont.Weight.DemiBold)
        p.setFont(f)
        w = p.fontMetrics().horizontalAdvance(text) + 16
        r = QRectF(at.x(), at.y(), w, 20)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.ACCENT) if hot else QColor(17, 19, 22, 220))
        p.drawRoundedRect(r, 3, 3)
        p.setPen(QColor("#17130B") if hot else QColor(T.TEXT_2))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)

    def _lens(self, p: QPainter, px: int, py: int, lp: QPointF) -> None:
        n, z = self.LENS, self.ZOOM
        size = n * z
        card_w, card_h = max(size + 2, 196), size + 96
        x = lp.x() + 24
        y = lp.y() + 24
        if x + card_w > self.width():
            x = lp.x() - 24 - card_w
        if y + card_h > self.height():
            y = lp.y() - 24 - card_h
        card = QRectF(x, y, card_w, card_h)
        p.setPen(QPen(QColor(T.BORDER_2), 1))
        p.setBrush(QColor(T.SURFACE))
        p.drawRoundedRect(card, 5, 5)
        half = n // 2
        fx, fy = px - self.ox, py - self.oy
        h, w = self.frame.shape[:2]
        for j in range(n):
            for i in range(n):
                sx, sy = fx - half + i, fy - half + j
                if 0 <= sx < w and 0 <= sy < h:
                    b, g, r = self.frame[sy, sx, :3]
                    c = QColor(int(r), int(g), int(b))
                else:
                    c = QColor(0, 0, 0)
                p.fillRect(QRectF(x + 1 + (card_w - 2 - size) / 2 + i * z, y + 1 + j * z, z, z), c)
        lx = x + 1 + (card_w - 2 - size) / 2
        p.setPen(QPen(QColor(0, 0, 0, 60), 1))
        for k in range(1, n):
            p.drawLine(QPointF(lx + k * z, y + 1), QPointF(lx + k * z, y + 1 + size))
            p.drawLine(QPointF(lx, y + 1 + k * z), QPointF(lx + size, y + 1 + k * z))
        p.setPen(QPen(QColor(T.ACCENT), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(lx + half * z, y + 1 + half * z, z, z))
        # readout
        rgb = self._rgb_at(px, py)
        ty = y + size + 10
        p.fillRect(QRectF(x + 10, ty + 2, 22, 22), QColor(*rgb))
        p.setPen(QPen(QColor(T.BORDER_2), 1))
        p.drawRect(QRectF(x + 10, ty + 2, 22, 22))
        p.setFont(T.mono_font(8.2))
        p.setPen(QColor(T.TEXT))
        p.drawText(QRectF(x + 40, ty, card_w - 44, 14), Qt.AlignmentFlag.AlignLeft, f"rgb {rgb[0]} {rgb[1]} {rgb[2]}")
        p.setPen(QColor(T.TEXT_3))
        p.drawText(QRectF(x + 40, ty + 13, card_w - 44, 14), Qt.AlignmentFlag.AlignLeft,
                   "#%02X%02X%02X" % rgb)
        t = self.hover
        p.setFont(T.mono_font(8.0))
        if t:
            cx, cy = px - t.rect.left, py - t.rect.top
            p.setPen(QColor(T.TEXT_2))
            p.drawText(QRectF(x + 10, ty + 32, card_w - 14, 14), Qt.AlignmentFlag.AlignLeft, f"client {cx}, {cy}")
            p.drawText(QRectF(x + 10, ty + 46, card_w - 14, 14), Qt.AlignmentFlag.AlignLeft,
                       f"ratio  {cx / t.rect.w:.4f}, {cy / t.rect.h:.4f}")
            p.setPen(QColor(T.TEXT_3))
            p.drawText(QRectF(x + 10, ty + 60, card_w - 14, 14), Qt.AlignmentFlag.AlignLeft,
                       f"{t.rect.w}×{t.rect.h}")
        else:
            p.setPen(QColor(T.TEXT_3))
            p.drawText(QRectF(x + 10, ty + 32, card_w - 14, 30), Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
                       "outside a game window")

    def _hintbar(self, p: QPainter) -> None:
        if self.mode == "window":
            text = self.hint or "Click the game window to bind  ·  Esc to cancel"
        else:
            text = self.hint or "Click to pick a point  ·  Drag to capture a template  ·  Esc to cancel"
        p.setFont(T.ui_font(9.2, QFont.Weight.DemiBold))
        w = p.fontMetrics().horizontalAdvance(text) + 36
        r = QRectF((self.width() - w) / 2, 18, w, 34)
        p.setPen(QPen(QColor(T.BORDER_2), 1))
        p.setBrush(QColor(17, 19, 22, 235))
        p.drawRoundedRect(r, 6, 6)
        p.setPen(QColor(T.TEXT))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
