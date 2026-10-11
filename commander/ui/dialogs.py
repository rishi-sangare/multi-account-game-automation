"""Dialogs: add/edit account, save a picked point/template into a preset."""
from __future__ import annotations

import re
import shlex
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton, QSpinBox,
                               QVBoxLayout, QWidget)

from .. import config as C
from .. import imaging, paths
from . import theme as T
from .picker import PickResult
from .widgets import caps_label, hline


def _title(text: str, sub: str = "") -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 6)
    v.setSpacing(2)
    t = QLabel(text)
    t.setFont(T.ui_font(12, QFont.Weight.DemiBold))
    v.addWidget(t)
    if sub:
        s = QLabel(sub)
        s.setWordWrap(True)
        s.setStyleSheet(f"color:{T.TEXT_3};")
        v.addWidget(s)
    return w


def _buttons(dlg: QDialog, ok_text: str) -> QHBoxLayout:
    h = QHBoxLayout()
    h.addStretch(1)
    cancel = QPushButton("Cancel")
    cancel.clicked.connect(dlg.reject)
    ok = QPushButton(ok_text)
    ok.setProperty("kind", "primary")
    ok.setDefault(True)
    ok.clicked.connect(dlg.accept)
    h.addWidget(cancel)
    h.addWidget(ok)
    dlg.ok_button = ok
    return h


def _form() -> QFormLayout:
    f = QFormLayout()
    f.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    f.setHorizontalSpacing(14)
    f.setVerticalSpacing(9)
    return f


def _lbl(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setStyleSheet(f"color:{T.TEXT_2};")
    return lb


# --------------------------------------------------------------------------- account


class AccountDialog(QDialog):
    def __init__(self, parent, account: C.Account | None, existing_ids: set[str], next_slot: int):
        super().__init__(parent)
        self.setWindowTitle("Account")
        self.setMinimumWidth(500)
        self.existing_ids = existing_ids - ({account.id} if account else set())
        self.editing = account
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(10)
        v.addWidget(_title("Edit account" if account else "Add account",
                           "Each account is one game client window. Launch mode starts the client for you; "
                           "attach mode binds to a window you opened and logged in yourself."))
        f = _form()
        self.name = QLineEdit(account.name if account else "")
        self.name.setPlaceholderText("e.g. Tanker07")
        self.ident = QLineEdit(account.id if account else "")
        self.ident.setPlaceholderText("auto")
        self.ident.setEnabled(account is None)
        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(account.enabled if account else True)
        f.addRow(_lbl("Name"), self.name)
        f.addRow(_lbl("ID"), self.ident)
        f.addRow(QLabel(""), self.enabled)
        v.addLayout(f)
        v.addWidget(hline())

        v.addWidget(caps_label("Client"))
        src_row = QHBoxLayout()
        self.src_mock = QRadioButton("Bundled mock")
        self.src_exe = QRadioButton("Launch executable")
        self.src_attach = QRadioButton("Attach to window")
        grp = QButtonGroup(self)
        for b in (self.src_mock, self.src_exe, self.src_attach):
            grp.addButton(b)
            src_row.addWidget(b)
        src_row.addStretch(1)
        v.addLayout(src_row)
        f2 = _form()
        exe_row = QHBoxLayout()
        self.exe = QLineEdit(account.exe if account and account.exe != "{mock}" else "")
        self.exe.setPlaceholderText(r"C:\ProTanki\acc1\ProTanki.exe")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        exe_row.addWidget(self.exe, 1)
        exe_row.addWidget(browse)
        self.args = QLineEdit(" ".join(shlex.quote(a) for a in account.args) if account else "")
        self.workdir = QLineEdit(account.workdir or "" if account else "")
        self.workdir.setPlaceholderText("default: the executable's folder")
        self.title_rx = QLineEdit(account.window_title or "" if account else "")
        self.title_rx.setPlaceholderText("regex, e.g. ^ProTanki")
        self.exe_label = _lbl("Executable")
        f2.addRow(self.exe_label, exe_row)
        self.args_label = _lbl("Arguments")
        f2.addRow(self.args_label, self.args)
        self.workdir_label = _lbl("Working dir")
        f2.addRow(self.workdir_label, self.workdir)
        self.title_label = _lbl("Window title")
        f2.addRow(self.title_label, self.title_rx)
        self._exe_widgets = [self.exe, browse, self.exe_label, self.workdir, self.workdir_label]
        v.addLayout(f2)
        v.addWidget(hline())

        v.addWidget(caps_label("Automation"))
        f3 = _form()
        self.preset = QComboBox()
        self.preset.addItems(C.list_presets())
        if account:
            self.preset.setCurrentText(account.preset)
        self.input_mode = QComboBox()
        self.input_mode.addItem("PostMessage  ·  background, parallel", "post")
        self.input_mode.addItem("SendInput  ·  real mouse, one at a time", "sendinput")
        self.input_mode.setCurrentIndex(1 if account and account.input_mode == "sendinput" else 0)
        self.slot = QSpinBox()
        self.slot.setRange(1, 40)
        self.slot.setValue((account.slot + 1) if account and account.slot is not None else next_slot + 1)
        f3.addRow(_lbl("Preset"), self.preset)
        f3.addRow(_lbl("Input"), self.input_mode)
        f3.addRow(_lbl("Grid slot"), self.slot)
        v.addLayout(f3)
        self.err = QLabel("")
        self.err.setStyleSheet("color:#F08A8D;")
        v.addWidget(self.err)
        v.addLayout(_buttons(self, "Save account"))

        if account is None or account.exe == "{mock}":
            self.src_mock.setChecked(True)
        elif account.mode == "attach":
            self.src_attach.setChecked(True)
        else:
            self.src_exe.setChecked(True)
        grp.buttonToggled.connect(lambda *_: self._sync())
        self._sync()

    def _sync(self) -> None:
        launch = self.src_exe.isChecked()
        attach = self.src_attach.isChecked()
        for w in self._exe_widgets:
            w.setVisible(launch)
        self.args.setVisible(not attach)
        self.args_label.setVisible(not attach)
        self.title_rx.setVisible(attach)
        self.title_label.setVisible(attach)
        if self.src_mock.isChecked() and self.preset.findText("mock-lobby") >= 0 and not self.editing:
            self.preset.setCurrentText("mock-lobby")

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose the game executable", "", "Programs (*.exe)")
        if path:
            self.exe.setText(path.replace("/", "\\"))

    def accept(self) -> None:
        try:
            self.result_account = self._build()
        except ValueError as e:
            self.err.setText(str(e))
            return
        super().accept()

    def _build(self) -> C.Account:
        name = self.name.text().strip()
        if not name:
            raise ValueError("Name is required")
        ident = self.ident.text().strip() or re.sub(r"[^A-Za-z0-9_\-]", "", name.replace(" ", "_"))[:24] or "acc"
        base, n = ident, 2
        while ident in self.existing_ids:
            ident, n = f"{base}{n}", n + 1
        mode = "attach" if self.src_attach.isChecked() else "launch"
        exe = "{mock}" if self.src_mock.isChecked() else (self.exe.text().strip() if mode == "launch" else "")
        if self.src_exe.isChecked() and not exe:
            raise ValueError("Choose the game executable")
        if mode == "attach" and not self.title_rx.text().strip():
            raise ValueError("Attach mode needs a window title pattern (or bind it later with 'Bind window')")
        try:
            args = shlex.split(self.args.text(), posix=False) if mode == "launch" else []
        except ValueError as e:
            raise ValueError(f"Arguments: {e}") from e
        return C.Account(id=ident, name=name, enabled=self.enabled.isChecked(), mode=mode, exe=exe, args=args,
                         workdir=self.workdir.text().strip() or None,
                         window_title=self.title_rx.text().strip() or None, preset=self.preset.currentText(),
                         input_mode=self.input_mode.currentData(), slot=self.slot.value() - 1)


# --------------------------------------------------------------------------- picked point


class _Swatch(QWidget):
    def __init__(self, rgb):
        super().__init__()
        self.rgb = rgb
        self.setFixedSize(38, 38)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QColor(T.BORDER_2))
        p.setBrush(QColor(*self.rgb))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 5, 5)


class PickSaveDialog(QDialog):
    """Write a picked point (and optionally a trigger check) or a template into a preset."""

    def __init__(self, parent, res: PickResult, preset_name: str):
        super().__init__(parent)
        self.res = res
        self.setWindowTitle("Save pick")
        self.setMinimumWidth(470)
        region = res.region is not None
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(10)
        v.addWidget(_title("Save template region" if region else "Save picked point",
                           f"Picked on {res.target.name}"))
        info = QHBoxLayout()
        info.addWidget(_Swatch(res.rgb))
        txt = QLabel(f"<span style='font-family:{T.MONO_FAMILY}'>rgb {res.rgb[0]} {res.rgb[1]} {res.rgb[2]}"
                     f"&nbsp;&nbsp;·&nbsp;&nbsp;client {res.cx}, {res.cy}<br>"
                     f"<span style='color:{T.TEXT_3}'>ratio {res.rx:.4f}, {res.ry:.4f}"
                     f"&nbsp;&nbsp;·&nbsp;&nbsp;window {res.client_w}×{res.client_h}"
                     + (f"&nbsp;&nbsp;·&nbsp;&nbsp;region {res.region[2]-res.region[0]}×{res.region[3]-res.region[1]}"
                        if region else "") + "</span></span>")
        info.addWidget(txt, 1)
        v.addLayout(info)
        v.addWidget(hline())
        f = _form()
        self.preset = QComboBox()
        self.preset.addItems(C.list_presets())
        self.preset.setCurrentText(preset_name)
        self.preset.currentTextChanged.connect(self._load)
        f.addRow(_lbl("Preset"), self.preset)
        self.point = QComboBox()
        self.point.setEditable(True)
        self.trigger = QComboBox()
        self.trigger.setEditable(True)
        if not region:
            f.addRow(_lbl("Point name"), self.point)
            self.use_trig = QCheckBox("Also use this colour as a trigger check")
            self.use_trig.setChecked(True)
            f.addRow(QLabel(""), self.use_trig)
            f.addRow(_lbl("Trigger"), self.trigger)
            self.tol = QSpinBox()
            self.tol.setRange(0, 120)
            self.tol.setValue(20)
            f.addRow(_lbl("Tolerance"), self.tol)
            self.replace = QCheckBox("Replace the trigger's other checks")
            f.addRow(QLabel(""), self.replace)
        else:
            f.addRow(_lbl("Trigger"), self.trigger)
            self.thr = QDoubleSpinBox()
            self.thr.setRange(0.5, 0.99)
            self.thr.setSingleStep(0.01)
            self.thr.setValue(0.86)
            f.addRow(_lbl("Match threshold"), self.thr)
            self.pad = QSpinBox()
            self.pad.setRange(0, 200)
            self.pad.setValue(12)
            self.pad.setSuffix(" px search margin")
            f.addRow(_lbl("Search area"), self.pad)
        v.addLayout(f)
        self.err = QLabel("")
        self.err.setStyleSheet("color:#F08A8D;")
        v.addWidget(self.err)
        v.addLayout(_buttons(self, "Save to preset"))
        self._load(preset_name)

    def _load(self, name: str) -> None:
        try:
            self.p = C.load_preset(name)
        except C.ConfigError as e:
            self.err.setText(str(e))
            return
        cur_pt, cur_tr = self.point.currentText(), self.trigger.currentText()
        self.point.clear()
        self.point.addItems(list(self.p.points))
        self.trigger.clear()
        self.trigger.addItems(list(self.p.triggers))
        nearest = min(self.p.points.items(), key=lambda kv: (kv[1].x - self.res.rx) ** 2 + (kv[1].y - self.res.ry) ** 2,
                      default=None)
        self.point.setCurrentText(cur_pt or (nearest[0] if nearest else "new_point"))
        if cur_tr:
            self.trigger.setCurrentText(cur_tr)
        elif nearest:
            for tn, tr in self.p.triggers.items():
                if any(c.at == nearest[0] for c in tr.checks):
                    self.trigger.setCurrentText(tn)
                    break

    def accept(self) -> None:
        try:
            self._save()
        except (ValueError, C.ConfigError) as e:
            self.err.setText(str(e))
            return
        super().accept()

    def _save(self) -> None:
        p = self.p
        r = self.res
        data = p.model_dump(mode="json", exclude_none=True)
        trig = self.trigger.currentText().strip()
        if not re.fullmatch(r"[A-Za-z0-9_]+", trig or "x"):
            raise ValueError("Trigger name: letters, digits and _ only")
        if r.region is None:
            name = self.point.currentText().strip()
            if not re.fullmatch(r"[A-Za-z0-9_]+", name):
                raise ValueError("Point name: letters, digits and _ only")
            data["points"][name] = {"x": round(r.rx, 4), "y": round(r.ry, 4)}
            if self.use_trig.isChecked() and trig:
                check = {"at": name, "rgb": list(r.rgb), "tol": self.tol.value()}
                t = data["triggers"].get(trig)
                if t is None or t.get("type") != "pixels" or self.replace.isChecked():
                    keep = {k: t[k] for k in ("debounce", "mode") if t and k in t and t.get("type") == "pixels"}
                    data["triggers"][trig] = {"type": "pixels", **keep, "checks": [check]}
                else:
                    checks = [c for c in t["checks"] if c.get("at") != name]
                    t["checks"] = checks + [check]
        else:
            l, tp, rr, b = r.region
            pad = self.pad.value()
            fname = f"{p.name}_{trig}_{int(time.time())}.png"
            paths.TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
            imaging.save_png(paths.TEMPLATES_DIR / fname, r.region_img)
            cw, ch = r.client_w, r.client_h
            data["triggers"][trig] = {
                "type": "template", "img": fname, "img_client_w": cw, "thr": round(self.thr.value(), 2),
                "region": [round(max(l - pad, 0) / cw, 4), round(max(tp - pad, 0) / ch, 4),
                           round(min(rr + pad, cw) / cw, 4), round(min(b + pad, ch) / ch, 4)]}
        new = C.Preset.model_validate(data)
        new.name = p.name
        C.save_preset(new)
        self.saved_preset = new.name


def confirm(parent, title: str, text: str, ok: str = "Confirm", danger: bool = False) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    yes = box.addButton(ok, QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    if not danger:
        yes.setProperty("kind", "primary")
    box.exec()
    return box.clickedButton() is yes
