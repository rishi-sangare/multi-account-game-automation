"""Inspector tabs: LIVE (what the engine sees), PRESET, SETTINGS, LOG."""
from __future__ import annotations

import html
import json
import os
import time

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMenu, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .. import config as C
from .. import paths
from ..engine import AccountSnap
from ..logbuffer import RING
from ..triggers import color_dist
from ..win32 import capture
from ..win32 import windows as W
from . import theme as T
from .widgets import Lamp, StatePill, caps_label, hline

# --------------------------------------------------------------------------- preset cache

_preset_cache: dict[str, tuple[float, C.Preset | None]] = {}


def cached_preset(name: str) -> C.Preset | None:
    p = C.preset_path(name)
    m = p.stat().st_mtime if p.exists() else 0.0
    hit = _preset_cache.get(name)
    if hit and hit[0] == m:
        return hit[1]
    try:
        preset = C.load_preset(name)
    except C.ConfigError:
        preset = None
    _preset_cache[name] = (m, preset)
    return preset


# --------------------------------------------------------------------------- live preview


class Preview(QWidget):
    """Live capture of the selected client with every preset point drawn on top."""

    def __init__(self):
        super().__init__()
        self.img: np.ndarray | None = None
        self.qimg: QImage | None = None
        self.preset: C.Preset | None = None
        self.message = "No window bound"
        self.setMinimumHeight(150)
        self.setMaximumHeight(300)

    def set_frame(self, img: np.ndarray | None, preset: C.Preset | None, message: str = "") -> None:
        self.img, self.preset, self.message = img, preset, message
        if img is not None:
            rgb = np.ascontiguousarray(img[:, :, [2, 1, 0]])
            self._rgb = rgb
            self.qimg = QImage(rgb.data, rgb.shape[1], rgb.shape[0], 3 * rgb.shape[1], QImage.Format.Format_RGB888)
        else:
            self.qimg = None
        self.update()

    def heightForWidth(self, w: int) -> int:
        return int(w * 0.75)

    def hasHeightForWidth(self) -> bool:
        return True

    def _check_states(self) -> dict[str, bool | None]:
        """point name -> True (all its checks match) / False / None (click-only point)."""
        out: dict[str, bool | None] = {}
        if self.preset is None or self.img is None:
            return out
        h, w = self.img.shape[:2]
        for trig in self.preset.triggers.values():
            for c in trig.checks:
                if c.at is None:
                    continue
                pt = self.preset.points[c.at]
                x, y = min(int(pt.x * w), w - 1), min(int(pt.y * h), h - 1)
                b, g, r = self.img[y, x, :3]
                ok = color_dist((int(r), int(g), int(b)), tuple(c.rgb)) <= c.tol
                out[c.at] = ok if out.get(c.at) is None else (out[c.at] and ok)
        for name in self.preset.points:
            out.setdefault(name, None)
        return out

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(T.BORDER), 1))
        p.setBrush(QColor(T.SURFACE))
        p.drawRoundedRect(r, 4, 4)
        if self.qimg is None:
            p.setPen(QColor(T.TEXT_3))
            p.setFont(T.ui_font(8.6))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.message)
            return
        iw, ih = self.qimg.width(), self.qimg.height()
        scale = min((r.width() - 2) / iw, (r.height() - 2) / ih)
        tw, th = iw * scale, ih * scale
        tr = QRectF(r.center().x() - tw / 2, r.center().y() - th / 2, tw, th)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(tr, self.qimg)
        if self.preset is None:
            return
        states = self._check_states()
        p.setFont(T.mono_font(7.0))
        for name, pt in self.preset.points.items():
            c = QPointF(tr.left() + pt.x * tw, tr.top() + pt.y * th)
            st = states.get(name)
            col = QColor(T.OK) if st else (QColor("#F06A6E") if st is False else QColor(T.ACCENT))
            p.setPen(QPen(QColor(0, 0, 0, 160), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, 5, 5)
            p.setPen(QPen(col, 1.6))
            p.drawEllipse(c, 5, 5)
            p.drawLine(QPointF(c.x() - 8, c.y()), QPointF(c.x() - 3, c.y()))
            p.drawLine(QPointF(c.x() + 3, c.y()), QPointF(c.x() + 8, c.y()))
            label_r = QRectF(c.x() + 8, c.y() - 15, 120, 12)
            p.setPen(QColor(0, 0, 0, 200))
            p.drawText(label_r.translated(1, 1), Qt.AlignmentFlag.AlignLeft, name)
            p.setPen(col)
            p.drawText(label_r, Qt.AlignmentFlag.AlignLeft, name)


# --------------------------------------------------------------------------- LIVE tab


class LiveTab(QWidget):
    request = Signal(str, str)        # (action, account id)

    def __init__(self):
        super().__init__()
        self.acc: AccountSnap | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(12)

        head = QHBoxLayout()
        self.title = QLabel("—")
        self.title.setFont(T.ui_font(12.5, QFont.Weight.DemiBold))
        self.pill = StatePill()
        head.addWidget(self.title)
        head.addSpacing(6)
        head.addWidget(self.pill)
        head.addStretch(1)
        self.b_focus = QPushButton(" Focus")
        self.b_focus.setIcon(T.icon("eye"))
        self.b_test = QPushButton(" Test")
        self.b_test.setIcon(T.icon("bolt"))
        self.b_bind = QPushButton(" Bind window")
        self.b_bind.setIcon(T.icon("link"))
        for b in (self.b_bind, self.b_test, self.b_focus):
            b.setProperty("kind", "ghost")
            head.addWidget(b)
        v.addLayout(head)
        self.b_focus.clicked.connect(lambda: self._emit("focus"))
        self.b_bind.clicked.connect(lambda: self._emit("bind"))
        self.b_test.clicked.connect(self._test_menu)

        cap_row = QHBoxLayout()
        cap_row.addWidget(caps_label("What the engine sees"))
        cap_row.addStretch(1)
        self.res_label = QLabel("")
        self.res_label.setFont(T.mono_font(7.6))
        self.res_label.setStyleSheet(f"color:{T.TEXT_3};")
        cap_row.addWidget(self.res_label)
        v.addLayout(cap_row)
        self.preview = Preview()
        v.addWidget(self.preview)

        v.addWidget(caps_label("Triggers"))
        self.trig_box = QVBoxLayout()
        self.trig_box.setSpacing(2)
        v.addLayout(self.trig_box)
        self.trig_rows: dict[str, tuple[Lamp, QLabel, QLabel]] = {}

        v.addWidget(hline())
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        self.fields: dict[str, QLabel] = {}
        keys = [("Window", "hwnd"), ("Process", "pid"), ("Client", "client"), ("Input", "input"),
                ("Clicks", "clicks"), ("Retries", "retries"), ("Last reaction", "react"), ("Last confirm", "confirm")]
        for i, (label, key) in enumerate(keys):
            r, c = divmod(i, 2)
            k = QLabel(label)
            k.setStyleSheet(f"color:{T.TEXT_3};")
            val = QLabel("—")
            val.setFont(T.mono_font(8.4))
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(k, r, c * 2)
            grid.addWidget(val, r, c * 2 + 1)
            self.fields[key] = val
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        v.addLayout(grid)
        v.addStretch(1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._grab)
        self.timer.start(250)

    def _emit(self, action: str) -> None:
        if self.acc:
            self.request.emit(action, self.acc.id)

    def _test_menu(self) -> None:
        if not self.acc:
            return
        m = QMenu(self)
        preset = cached_preset(self.acc.preset)
        if preset:
            for name in preset.sequences:
                m.addAction(f"Run sequence  ·  {name}", lambda n=name: self.request.emit(f"seq:{n}", self.acc.id))
            m.addSeparator()
            for name in preset.points:
                m.addAction(f"Click point  ·  {name}", lambda n=name: self.request.emit(f"click:{n}", self.acc.id))
        m.exec(self.b_test.mapToGlobal(self.b_test.rect().bottomLeft()))

    def set_account(self, acc: AccountSnap | None) -> None:
        self.acc = acc
        if acc is None:
            self.title.setText("No account selected")
            return
        self.title.setText(acc.name)
        self.pill.set(acc.state, "")
        f = self.fields
        f["hwnd"].setText(f"0x{acc.hwnd:X}" if acc.hwnd else "—")
        f["pid"].setText(str(acc.pid) if acc.pid else "—")
        f["client"].setText(f"{acc.client[0]}×{acc.client[1]}" if acc.hwnd else "—")
        f["input"].setText(acc.input_mode + ("  (escalated)" if acc.escalated else ""))
        f["clicks"].setText(str(acc.clicks))
        f["retries"].setText(str(acc.retries))
        f["react"].setText(f"≤{acc.react_last:.0f} ms" if acc.react_last is not None else "—")
        f["confirm"].setText(f"{acc.confirm_last:.0f} ms" if acc.confirm_last is not None else "—")
        self.b_focus.setEnabled(bool(acc.hwnd))
        self._sync_triggers(acc)

    def _sync_triggers(self, acc: AccountSnap) -> None:
        preset = cached_preset(acc.preset)
        names = list(preset.triggers) if preset else []
        if names != list(self.trig_rows):
            while self.trig_box.count():
                item = self.trig_box.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self.trig_rows.clear()
            for n in names:
                row = QWidget()
                h = QHBoxLayout(row)
                h.setContentsMargins(0, 3, 0, 3)
                lamp = Lamp()
                name = QLabel(n)
                name.setFont(T.mono_font(8.6))
                desc = QLabel("")
                desc.setStyleSheet(f"color:{T.TEXT_3};")
                h.addWidget(lamp)
                h.addSpacing(4)
                h.addWidget(name)
                h.addStretch(1)
                h.addWidget(desc)
                self.trig_box.addWidget(row)
                self.trig_rows[n] = (lamp, name, desc)
        for n, (lamp, _, desc) in self.trig_rows.items():
            t = preset.triggers[n]
            lamp.set(acc.obs.get(n) if acc.hwnd and acc.obs else None)
            if t.type == "pixels":
                d = f"{len(t.checks)} pixel{'s' if len(t.checks) > 1 else ''} · {t.mode}"
            elif t.type == "template":
                d = f"template ≥{t.thr:.2f}"
            else:
                d = f"not {t.of}"
            if t.debounce > 1:
                d += f" · ×{t.debounce}"
            desc.setText(d)

    def _grab(self) -> None:
        if not self.isVisible() or self.acc is None:
            return
        acc = self.acc
        preset = cached_preset(acc.preset)
        if not acc.hwnd or not W.is_window(acc.hwnd):
            self.preview.set_frame(None, preset, "No window bound — press Start or Bind window")
            self.res_label.setText("")
            return
        try:
            img = capture.grab(W.client_rect_screen(acc.hwnd))
        except Exception:
            return
        self.res_label.setText(f"{img.shape[1]}×{img.shape[0]} px")
        self.preview.set_frame(img.copy(), preset)


# --------------------------------------------------------------------------- PRESET tab


class JsonDialog(QDialog):
    def __init__(self, parent, name: str):
        super().__init__(parent)
        self.name = name
        self.setWindowTitle(f"Preset · {name}")
        self.resize(640, 640)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 14)
        hdr = QLabel(f"{C.preset_path(name)}")
        hdr.setFont(T.mono_font(8))
        hdr.setStyleSheet(f"color:{T.TEXT_3};")
        v.addWidget(hdr)
        self.edit = QPlainTextEdit(C.preset_path(name).read_text(encoding="utf-8"))
        self.edit.setFont(T.mono_font(9))
        self.edit.setTabStopDistance(28)
        v.addWidget(self.edit, 1)
        self.err = QLabel("")
        self.err.setWordWrap(True)
        self.err.setStyleSheet("color:#F08A8D;")
        v.addWidget(self.err)
        h = QHBoxLayout()
        h.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Validate & save")
        save.setProperty("kind", "primary")
        save.clicked.connect(self._save)
        h.addWidget(cancel)
        h.addWidget(save)
        v.addLayout(h)

    def _save(self) -> None:
        try:
            data = json.loads(self.edit.toPlainText())
            p = C.Preset.model_validate(data)
        except json.JSONDecodeError as e:
            self.err.setText(f"JSON error at line {e.lineno}: {e.msg}")
            return
        except Exception as e:                                 # pydantic ValidationError
            self.err.setText(str(e).splitlines()[0] + " — " + " ".join(str(e).splitlines()[1:3]))
            return
        p.name = self.name
        C.save_preset(p)
        self.accept()


class PresetTab(QWidget):
    request = Signal(str, str)        # (action, account id)
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.acc: AccountSnap | None = None
        self.preset_name = ""
        self._loaded_mtime = 0.0
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(QLabel("Preset"))
        self.combo = QComboBox()
        self.combo.setMinimumWidth(160)
        self.combo.activated.connect(self._assign)
        top.addWidget(self.combo)
        top.addStretch(1)
        self.b_pick = QPushButton(" Pick point")
        self.b_pick.setIcon(T.icon("crosshair"))
        self.b_json = QPushButton(" Edit JSON")
        self.b_json.setIcon(T.icon("edit"))
        for b in (self.b_pick, self.b_json):
            b.setProperty("kind", "ghost")
            top.addWidget(b)
        self.b_pick.clicked.connect(lambda: self.acc and self.request.emit("pick", self.acc.id))
        self.b_json.clicked.connect(self._edit_json)
        v.addLayout(top)
        self.desc = QLabel("")
        self.desc.setWordWrap(True)
        self.desc.setStyleSheet(f"color:{T.TEXT_3};")
        v.addWidget(self.desc)

        v.addWidget(caps_label("Points  ·  ratios of the client area"))
        self.points = QTableWidget(0, 4)
        self.points.setHorizontalHeaderLabels(["NAME", "X", "Y", ""])
        self.points.verticalHeader().setVisible(False)
        self.points.setShowGrid(False)
        self.points.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.points.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        hh = self.points.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c, w in ((1, 74), (2, 74), (3, 70)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            self.points.setColumnWidth(c, w)
        self.points.verticalHeader().setDefaultSectionSize(32)
        self.points.setStyleSheet(f"QTableWidget{{background:{T.BG};}} QTableWidget::item{{border-bottom:1px solid {T.BORDER};"
                                  f"padding-left:6px;}}")
        self.points.itemChanged.connect(self._point_edited)
        v.addWidget(self.points, 2)

        v.addWidget(caps_label("Sequences"))
        self.seqs = QLabel("")
        self.seqs.setFont(T.mono_font(8.2))
        self.seqs.setWordWrap(True)
        self.seqs.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(self.seqs)
        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{T.TEXT_3};")
        v.addWidget(self.status)
        self._building = False

    def set_account(self, acc: AccountSnap | None) -> None:
        if acc is None:
            return
        changed = self.acc is None or acc.id != self.acc.id or acc.preset != self.preset_name
        self.acc = acc
        p = C.preset_path(acc.preset)
        m = p.stat().st_mtime if p.exists() else 0.0
        if changed or m != self._loaded_mtime:
            self._load(acc.preset)

    def _load(self, name: str) -> None:
        self.preset_name = name
        presets = C.list_presets()
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItems(presets)
        self.combo.setCurrentText(name)
        self.combo.blockSignals(False)
        p = C.preset_path(name)
        self._loaded_mtime = p.stat().st_mtime if p.exists() else 0.0
        preset = cached_preset(name)
        self._building = True
        self.points.setRowCount(0)
        if preset is None:
            self.desc.setText("This preset has an error. Use Edit JSON to fix it.")
            self._building = False
            return
        self.desc.setText(preset.description or "")
        for i, (pname, pt) in enumerate(preset.points.items()):
            self.points.insertRow(i)
            it = QTableWidgetItem(pname)
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            it.setFont(T.mono_font(8.6))
            self.points.setItem(i, 0, it)
            for c, val in ((1, pt.x), (2, pt.y)):
                vi = QTableWidgetItem(f"{val:.4f}")
                vi.setFont(T.mono_font(8.6))
                vi.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.points.setItem(i, c, vi)
            btn = QPushButton("Test")
            btn.setProperty("kind", "ghost")
            btn.setToolTip(f"Click '{pname}' on the selected account")
            btn.clicked.connect(lambda _=False, n=pname: self.acc and self.request.emit(f"click:{n}", self.acc.id))
            self.points.setCellWidget(i, 3, btn)
        seq_lines = []
        for sname, steps in preset.sequences.items():
            parts = []
            for st in steps:
                if st.click:
                    parts.append(f"<span style='color:{T.TEXT}'>click</span> {st.click}")
                elif st.key:
                    parts.append(f"<span style='color:{T.TEXT}'>key</span> {st.key}")
                else:
                    parts.append(f"<span style='color:{T.TEXT_3}'>wait {st.wait_ms}</span>")
            seq_lines.append(f"<span style='color:{T.ACCENT}'>{sname}</span>&nbsp;&nbsp;"
                             f"<span style='color:{T.TEXT_2}'>{' → '.join(parts)}</span>")
        self.seqs.setText("<br>".join(seq_lines))
        self._building = False

    def _point_edited(self, item: QTableWidgetItem) -> None:
        if self._building or item.column() not in (1, 2):
            return
        name = self.points.item(item.row(), 0).text()
        try:
            val = float(item.text())
            if not 0.0 <= val <= 1.0:
                raise ValueError
        except ValueError:
            self.status.setText("Values are ratios between 0 and 1")
            self._load(self.preset_name)
            return
        preset = C.load_preset(self.preset_name)
        pt = preset.points[name]
        if item.column() == 1:
            pt.x = val
        else:
            pt.y = val
        C.save_preset(preset)
        self.status.setText(f"Saved {name} → ({pt.x:.4f}, {pt.y:.4f})")
        self.changed.emit()

    def _assign(self, *_):
        if self.acc and self.combo.currentText() != self.acc.preset:
            self.request.emit(f"preset:{self.combo.currentText()}", self.acc.id)

    def _edit_json(self) -> None:
        if not self.preset_name:
            return
        dlg = JsonDialog(self, self.preset_name)
        if dlg.exec():
            self._load(self.preset_name)
            self.changed.emit()


# --------------------------------------------------------------------------- SETTINGS tab


class SettingsTab(QWidget):
    applied = Signal(object)          # Settings

    def __init__(self, settings: C.Settings):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)

        def form() -> QFormLayout:
            f = QFormLayout()
            f.setHorizontalSpacing(16)
            f.setVerticalSpacing(8)
            f.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            return f

        def spin(lo, hi, val, suffix=""):
            s = QSpinBox()
            s.setRange(lo, hi)
            s.setValue(val)
            s.setSuffix(suffix)
            s.setMaximumWidth(130)
            return s

        def lbl(t):
            x = QLabel(t)
            x.setStyleSheet(f"color:{T.TEXT_2};")
            return x

        s = settings
        v.addWidget(caps_label("Engine"))
        f = form()
        self.poll = spin(10, 500, s.poll_ms, " ms")
        self.verify = spin(50, 3000, s.verify_ms, " ms")
        self.retries = spin(0, 10, s.max_retries)
        self.exit_mode = QComboBox()
        for k, t in (("leave_battle", "Leave battle (stay in lobby)"), ("close_client", "Close the client"),
                     ("kill", "Kill the process")):
            self.exit_mode.addItem(t, k)
        self.exit_mode.setCurrentIndex(["leave_battle", "close_client", "kill"].index(s.exit_mode))
        self.relaunch = QCheckBox("Relaunch crashed clients")
        self.relaunch.setChecked(s.auto_relaunch)
        self.anti_afk = QCheckBox("Anti-AFK nudge while in battle")
        self.anti_afk.setChecked(s.anti_afk)
        self.rename = QCheckBox("Rename client windows to the account name")
        self.rename.setChecked(s.rename_windows)
        self.raise_cov = QCheckBox("Bring covered clients back on top")
        self.raise_cov.setChecked(s.raise_covered)
        f.addRow(lbl("Scan interval"), self.poll)
        f.addRow(lbl("Verify after"), self.verify)
        f.addRow(lbl("Click retries"), self.retries)
        f.addRow(lbl("Exit All does"), self.exit_mode)
        f.addRow(QLabel(""), self.relaunch)
        f.addRow(QLabel(""), self.anti_afk)
        f.addRow(QLabel(""), self.rename)
        f.addRow(QLabel(""), self.raise_cov)
        v.addLayout(f)
        v.addWidget(hline())

        v.addWidget(caps_label("Window grid"))
        f = form()
        L = s.layout
        self.cols = spin(1, 10, L.cols)
        self.cw = spin(160, 3840, L.client_w, " px")
        self.ch = spin(120, 2160, L.client_h, " px")
        self.gap = spin(0, 100, L.gap, " px")
        self.ox = spin(-10000, 10000, L.origin[0], " px")
        self.oy = spin(-10000, 10000, L.origin[1], " px")
        self.autotile = QCheckBox("Tile windows automatically when bound")
        self.autotile.setChecked(L.auto_tile)
        size_row = QHBoxLayout()
        size_row.addWidget(self.cw)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.ch)
        size_row.addStretch(1)
        org_row = QHBoxLayout()
        org_row.addWidget(self.ox)
        org_row.addWidget(QLabel(","))
        org_row.addWidget(self.oy)
        org_row.addStretch(1)
        f.addRow(lbl("Columns"), self.cols)
        f.addRow(lbl("Client size"), size_row)
        f.addRow(lbl("Gap"), self.gap)
        f.addRow(lbl("Origin"), org_row)
        f.addRow(QLabel(""), self.autotile)
        v.addLayout(f)
        v.addWidget(hline())

        v.addWidget(caps_label("Hotkeys  ·  system-wide"))
        f = form()
        self.hk_exit = QLineEdit(s.hotkeys.exit_all)
        self.hk_kill = QLineEdit(s.hotkeys.kill_all)
        self.hk_pause = QLineEdit(s.hotkeys.pause_all)
        for w in (self.hk_exit, self.hk_kill, self.hk_pause):
            w.setFont(T.mono_font(8.6))
            w.setMaximumWidth(180)
        f.addRow(lbl("Exit all"), self.hk_exit)
        f.addRow(lbl("Kill all"), self.hk_kill)
        f.addRow(lbl("Pause all"), self.hk_pause)
        v.addLayout(f)
        v.addWidget(hline())

        v.addWidget(caps_label("Telegram remote"))
        f = form()
        self.tg_on = QCheckBox("Enable /exitall /status /screenshot from Telegram")
        self.tg_on.setChecked(s.telegram.enabled)
        self.tg_token = QLineEdit(s.telegram.token)
        self.tg_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.tg_token.setPlaceholderText("bot token from @BotFather")
        self.tg_user = QLineEdit(str(s.telegram.allowed_user_id or ""))
        self.tg_user.setPlaceholderText("your numeric Telegram user id")
        f.addRow(QLabel(""), self.tg_on)
        f.addRow(lbl("Bot token"), self.tg_token)
        f.addRow(lbl("Allowed user"), self.tg_user)
        v.addLayout(f)

        self.err = QLabel("")
        self.err.setStyleSheet("color:#F08A8D;")
        v.addWidget(self.err)
        row = QHBoxLayout()
        row.addStretch(1)
        save = QPushButton(" Save & apply")
        save.setIcon(T.icon("save", "#17130B"))
        save.setProperty("kind", "primary")
        save.clicked.connect(self._save)
        row.addWidget(save)
        v.addLayout(row)
        v.addStretch(1)

    def _save(self) -> None:
        try:
            uid = int(self.tg_user.text().strip() or 0)
        except ValueError:
            self.err.setText("Telegram user id must be a number")
            return
        try:
            from ..win32.hotkeys import parse
            for w in (self.hk_exit, self.hk_kill, self.hk_pause):
                parse(w.text())
        except ValueError as e:
            self.err.setText(f"Hotkey: {e}")
            return
        s = C.Settings(
            poll_ms=self.poll.value(), verify_ms=self.verify.value(), max_retries=self.retries.value(),
            exit_mode=self.exit_mode.currentData(), auto_relaunch=self.relaunch.isChecked(),
            anti_afk=self.anti_afk.isChecked(), rename_windows=self.rename.isChecked(),
            raise_covered=self.raise_cov.isChecked(),
            layout=C.Layout(cols=self.cols.value(), client_w=self.cw.value(), client_h=self.ch.value(),
                            gap=self.gap.value(), origin=(self.ox.value(), self.oy.value()),
                            auto_tile=self.autotile.isChecked()),
            hotkeys=C.Hotkeys(exit_all=self.hk_exit.text().strip(), kill_all=self.hk_kill.text().strip(),
                              pause_all=self.hk_pause.text().strip()),
            telegram=C.Telegram(enabled=self.tg_on.isChecked(), token=self.tg_token.text().strip(),
                                allowed_user_id=uid))
        self.err.setText("")
        self.applied.emit(s)

    def set_exit_mode(self, mode: str) -> None:
        self.exit_mode.setCurrentIndex(["leave_battle", "close_client", "kill"].index(mode))


# --------------------------------------------------------------------------- LOG tab


class LogTab(QWidget):
    COLORS = {10: T.TEXT_3, 20: T.TEXT_2, 30: "#E2B046", 40: "#F06A6E", 50: "#F06A6E"}

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)
        top = QHBoxLayout()
        self.only_warn = QCheckBox("Warnings only")
        self.only_warn.toggled.connect(self._rebuild)
        top.addWidget(self.only_warn)
        top.addStretch(1)
        open_btn = QPushButton("Open log folder")
        open_btn.setProperty("kind", "ghost")
        open_btn.clicked.connect(lambda: os.startfile(str(paths.LOGS_DIR)))
        top.addWidget(open_btn)
        v.addLayout(top)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(1500)
        self.view.setFont(T.mono_font(8.3))
        self.view.setStyleSheet(f"QPlainTextEdit{{background:{T.BG};border:1px solid {T.BORDER};}}")
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        v.addWidget(self.view)
        self.seq = 0
        t = QTimer(self)
        t.timeout.connect(self.pull)
        t.start(150)

    def _line_html(self, ln) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(ln.t)) + f".{int((ln.t % 1) * 1000):03d}"
        color = self.COLORS.get(ln.level, T.TEXT_2)
        text = html.escape(ln.text)
        if " · " in text and ln.name == "engine":
            acc, rest = text.split(" · ", 1)
            text = f"<span style='color:{T.ACCENT}'>{acc}</span> <span style='color:{color}'>{rest}</span>"
        else:
            text = f"<span style='color:{color}'>{text}</span>"
        return f"<span style='color:{T.TEXT_3}'>{ts}</span>&nbsp;&nbsp;{text}"

    def pull(self) -> None:
        lines = RING.since(self.seq)
        if not lines:
            return
        self.seq = lines[-1].seq
        bar = self.view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        for ln in lines:
            if self.only_warn.isChecked() and ln.level < 30:
                continue
            self.view.appendHtml(self._line_html(ln))
        if at_bottom:
            self.view.moveCursor(QTextCursor.MoveOperation.End)
            bar.setValue(bar.maximum())

    def _rebuild(self) -> None:
        self.view.clear()
        self.seq = 0
        self.pull()


class Inspector(QTabWidget):
    def __init__(self, settings: C.Settings):
        super().__init__()
        self.setDocumentMode(True)
        self.live = LiveTab()
        self.preset = PresetTab()
        self.settings_tab = SettingsTab(settings)
        self.log = LogTab()
        self.addTab(self.live, "LIVE")
        self.addTab(self.preset, "PRESET")
        self.addTab(self.settings_tab, "SETTINGS")
        self.addTab(self.log, "LOG")

    def set_account(self, acc: AccountSnap | None) -> None:
        self.live.set_account(acc)
        if self.currentWidget() is self.preset or self.preset.acc is None:
            self.preset.set_account(acc)
