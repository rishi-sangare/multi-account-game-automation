"""The accounts table: one row per account, custom painted."""
from __future__ import annotations

import time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyledItemDelegate, QStyleOptionViewItem, QTableView

from ..engine import AccountSnap
from . import theme as T
from .widgets import paint_state_pill

COLS = ["#", "Account", "State", "Joins", "Missed", "Reaction", "Confirm", "Last event", ""]
C_NUM, C_ACC, C_STATE, C_JOINS, C_MISS, C_REACT, C_CONF, C_EVENT, C_ACT = range(9)
ROW_H = 40


def fmt_age(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m{sec % 60:02d}"
    return f"{sec // 3600}h{(sec % 3600) // 60:02d}"


def fmt_ms(v: float | None, prefix: str = "") -> str:
    return "—" if v is None else f"{prefix}{v:.0f}"


class AccountsModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows: tuple[AccountSnap, ...] = ()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLS[section].upper()
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.TextAlignmentRole:
            if section in (C_JOINS, C_MISS, C_REACT, C_CONF):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.UserRole and index.isValid():
            return self.rows[index.row()]
        return None

    def update_rows(self, rows: tuple[AccountSnap, ...]) -> None:
        if [r.id for r in rows] != [r.id for r in self.rows]:
            self.beginResetModel()
            self.rows = rows
            self.endResetModel()
        else:
            self.rows = rows
            if rows:
                self.dataChanged.emit(self.index(0, 0), self.index(len(rows) - 1, len(COLS) - 1))

    def row_of(self, acc_id: str) -> int:
        for i, r in enumerate(self.rows):
            if r.id == acc_id:
                return i
        return -1


class AccountsDelegate(QStyledItemDelegate):
    def __init__(self, view: "AccountsView"):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index):
        return QSize(80, ROW_H)

    # -- action hit boxes (column C_ACT)
    @staticmethod
    def action_rects(cell: QRect) -> dict[str, QRect]:
        s, gap = 26, 2
        x = cell.right() - 8 - 3 * s - 2 * gap
        y = cell.center().y() - s // 2
        return {name: QRect(x + i * (s + gap), y, s, s) for i, name in enumerate(("toggle", "focus", "more"))}

    def paint(self, p: QPainter, opt: QStyleOptionViewItem, index: QModelIndex) -> None:
        a: AccountSnap = index.data(Qt.ItemDataRole.UserRole)
        if a is None:
            return
        r = QRectF(opt.rect)
        row = index.row()
        selected = self.view.selectionModel().isRowSelected(row, QModelIndex())
        hovered = row == self.view.hover_row
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(T.SURFACE_2 if selected else (T.SURFACE if hovered else T.BG))
        p.fillRect(r, bg)
        p.setPen(QPen(QColor(T.BORDER), 1))
        p.drawLine(r.bottomLeft(), r.bottomRight())
        col = index.column()
        dim = not a.enabled
        text_c = QColor(T.TEXT_3 if dim else T.TEXT)

        if col == C_NUM:
            if selected:
                p.fillRect(QRectF(r.left(), r.top(), 2, r.height()), QColor(T.ACCENT))
            p.setFont(T.mono_font(8.2))
            p.setPen(QColor(T.TEXT_3))
            p.drawText(r.adjusted(12, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, f"{a.slot + 1:02d}")
        elif col == C_ACC:
            p.setFont(T.ui_font(9.2, QFont.Weight.DemiBold))
            p.setPen(text_c)
            p.drawText(QRectF(r.left() + 8, r.top() + 4, r.width() - 10, 18),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       QFontMetricsF(p.font()).elidedText(a.name, Qt.TextElideMode.ElideRight, r.width() - 16))
            meta = f"{a.preset} · {a.input_mode}" + (" ↑" if a.escalated else "")
            if a.pid:
                meta = f"pid {a.pid} · " + meta
            p.setFont(T.mono_font(7.4))
            p.setPen(QColor(T.TEXT_3))
            p.drawText(QRectF(r.left() + 8, r.top() + 21, r.width() - 10, 14),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       QFontMetricsF(p.font()).elidedText(meta, Qt.TextElideMode.ElideRight, r.width() - 16))
        elif col == C_STATE:
            suffix = fmt_age(a.state_age) if a.state not in ("STOPPED",) else ""
            paint_state_pill(p, r.adjusted(8, 0, 0, 0), a.state, suffix)
        elif col in (C_JOINS, C_MISS, C_REACT, C_CONF):
            p.setFont(T.mono_font(9.2))
            if col == C_JOINS:
                txt, c = str(a.joins), text_c
            elif col == C_MISS:
                txt = str(a.misses)
                c = QColor("#F08A8D") if a.misses else QColor(T.TEXT_3)
            elif col == C_REACT:
                txt = fmt_ms(a.react_avg, "≤")
                c = text_c if a.react_avg is not None else QColor(T.TEXT_3)
            else:
                txt = fmt_ms(a.confirm_last)
                c = QColor(T.TEXT_2) if a.confirm_last is not None else QColor(T.TEXT_3)
            p.setPen(c)
            unit = " ms" if col in (C_REACT, C_CONF) and txt != "—" else ""
            p.drawText(r.adjusted(0, 0, -12 - (18 if unit else 0), 0),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, txt)
            if unit:
                p.setFont(T.ui_font(7.6))
                p.setPen(QColor(T.TEXT_3))
                p.drawText(r.adjusted(0, 1, -12, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "ms")
        elif col == C_EVENT:
            age = time.time() - a.last_event_t
            p.setFont(T.ui_font(8.4))
            p.setPen(QColor(T.TEXT_2) if age < 4 else QColor(T.TEXT_3))
            ev = a.error or a.last_event
            if a.error:
                p.setPen(QColor("#F08A8D"))
            p.drawText(r.adjusted(10, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       QFontMetricsF(p.font()).elidedText(ev, Qt.TextElideMode.ElideRight, r.width() - 18))
        elif col == C_ACT:
            live = a.state in ("CONNECTING", "WATCHING", "JOINING", "IN_BATTLE", "LAUNCHING")
            hov = self.view.hover_action if hovered else None
            for name, rc in self.action_rects(opt.rect).items():
                rcf = QRectF(rc)
                if hov == name:
                    p.setPen(QPen(QColor(T.BORDER_2), 1))
                    p.setBrush(QColor(T.SURFACE_3))
                    p.drawRoundedRect(rcf.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
                icon = {"toggle": "pause" if live else "play", "focus": "eye", "more": "dots"}[name]
                color = T.TEXT if hov == name else T.TEXT_2
                if name == "toggle" and not live:
                    color = T.OK if hov == name else "#6FBF93"
                if name == "focus" and not a.hwnd:
                    color = "#3A4048"
                T.paint_icon(p, icon, QRectF(rc.center().x() - 8, rc.center().y() - 8, 16, 16), QColor(color))
        p.restore()


class AccountsView(QTableView):
    selected = Signal(str)
    action = Signal(str, str, QPoint)

    def __init__(self):
        super().__init__()
        self.model_ = AccountsModel()
        self.setModel(self.model_)
        self.delegate = AccountsDelegate(self)
        self.setItemDelegate(self.delegate)
        self.hover_row = -1
        self.hover_action: str | None = None
        self.setMouseTracking(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(ROW_H)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        hh = self.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hh.setMinimumSectionSize(30)
        widths = {C_NUM: 38, C_ACC: 136, C_STATE: 132, C_JOINS: 52, C_MISS: 60, C_REACT: 76, C_CONF: 72, C_ACT: 92}
        for c, w in widths.items():
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed if c in (C_NUM, C_ACT) else QHeaderView.ResizeMode.Interactive)
            self.setColumnWidth(c, w)
        hh.setSectionResizeMode(C_EVENT, QHeaderView.ResizeMode.Stretch)
        hh.setFixedHeight(30)
        self.selectionModel().selectionChanged.connect(self._sel_changed)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w = self.viewport().width()
        self.setColumnHidden(C_CONF, w < 760)
        self.setColumnHidden(C_EVENT, w < 640)

    def update_rows(self, rows) -> None:
        sel = self.current_id()
        self.model_.update_rows(rows)
        if sel and self.current_id() != sel:
            self.select_id(sel)
        elif not sel and rows:
            self.selectRow(0)
        self.viewport().update()

    def current_id(self) -> str | None:
        idx = self.selectionModel().selectedRows()
        if idx and idx[0].row() < len(self.model_.rows):
            return self.model_.rows[idx[0].row()].id
        return None

    def select_id(self, acc_id: str) -> None:
        r = self.model_.row_of(acc_id)
        if r >= 0:
            self.selectRow(r)

    def _sel_changed(self, *_):
        cid = self.current_id()
        if cid:
            self.selected.emit(cid)

    def mouseMoveEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        row = idx.row() if idx.isValid() else -1
        act = None
        if idx.isValid() and idx.column() == C_ACT:
            for name, rc in AccountsDelegate.action_rects(self.visualRect(idx)).items():
                if rc.contains(e.position().toPoint()):
                    act = name
        if (row, act) != (self.hover_row, self.hover_action):
            self.hover_row, self.hover_action = row, act
            self.setCursor(Qt.CursorShape.PointingHandCursor if act else Qt.CursorShape.ArrowCursor)
            self.viewport().update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self.hover_row, self.hover_action = -1, None
        self.viewport().update()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        if idx.isValid() and idx.column() == C_ACT and e.button() == Qt.MouseButton.LeftButton:
            for name, rc in AccountsDelegate.action_rects(self.visualRect(idx)).items():
                if rc.contains(e.position().toPoint()):
                    acc = self.model_.rows[idx.row()]
                    self.action.emit(acc.id, name, self.viewport().mapToGlobal(rc.bottomLeft()))
                    break
        super().mouseReleaseEvent(e)

    def contextMenuEvent(self, e):
        idx = self.indexAt(e.pos())
        if idx.isValid():
            self.selectRow(idx.row())
            self.action.emit(self.model_.rows[idx.row()].id, "more", e.globalPos())
