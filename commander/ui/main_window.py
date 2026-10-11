"""Main window: header, stat strip, toolbar, accounts table + inspector."""
from __future__ import annotations

import time

import win32gui
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton, QSplitter,
                               QStatusBar, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from .. import config as C
from .. import paths
from ..engine import Engine, EngineSnap
from ..win32 import windows as W
from ..win32.hotkeys import pretty
from . import theme as T
from .accounts_table import AccountsView
from .dialogs import AccountDialog, PickSaveDialog, confirm
from .inspector import Inspector
from .picker import PickerOverlay, PickTarget
from .widgets import Chip, ExitAllButton, Segmented, StatTile, caps_label, vline

LIVE = {"CONNECTING", "WATCHING", "JOINING", "IN_BATTLE", "EXITING", "LAUNCHING"}


class MainWindow(QMainWindow):
    def __init__(self, engine: Engine, settings: C.Settings, app_ctl):
        super().__init__()
        self.engine = engine
        self.settings = settings
        self.ctl = app_ctl                        # restarts hotkeys / telegram after settings change
        self.snap = EngineSnap()
        self._exit_flash_until = 0.0
        self._last_exit_seen: float | None = None
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(T.emblem_pixmap(32)))
        self.setMinimumSize(640, 560)

        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._build_header())
        v.addWidget(self._build_stats())
        v.addWidget(self._build_toolbar())

        self.table = AccountsView()
        self.inspector = Inspector(settings)
        self.split = QSplitter(Qt.Orientation.Vertical)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(self.table)
        self.split.addWidget(self.inspector)
        self.split.setStretchFactor(0, 6)
        self.split.setStretchFactor(1, 5)
        v.addWidget(self.split, 1)
        self._build_statusbar()

        self.table.selected.connect(lambda _id: self._refresh_inspector())
        self.table.action.connect(self._row_action)
        self.inspector.live.request.connect(self._inspector_request)
        self.inspector.preset.request.connect(self._inspector_request)
        self.inspector.preset.changed.connect(lambda: self.engine.submit("RELOAD"))
        self.inspector.settings_tab.applied.connect(self._apply_settings)
        self.inspector.currentChanged.connect(lambda _: self._refresh_inspector())

        for seq, fn in (("Ctrl+Shift+X", self._exit_all), ("F5", lambda: self.engine.submit("START")),
                        ("F6", lambda: self.engine.submit("PAUSE")), ("Ctrl+P", lambda: self._pick(None)),
                        ("Ctrl+N", self._add_account), ("Ctrl+T", lambda: self.engine.submit("TILE"))):
            QShortcut(QKeySequence(seq), self, activated=fn)
        self.b_start.setToolTip("Launch / resume every enabled account  (F5)")
        self.b_pause.setToolTip("Pause automation, keep clients open  (F6)")
        self.b_pick.setToolTip("Freeze the screen and pick a button position + colour  (Ctrl+P)")

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(125)

    # ------------------------------------------------------------------ building

    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(60)
        w.setStyleSheet(f"background:{T.SURFACE};")
        h = QHBoxLayout(w)
        h.setContentsMargins(16, 0, 12, 0)
        h.setSpacing(10)
        mark = QLabel()
        mark.setPixmap(T.emblem_pixmap(24))
        h.addWidget(mark)
        brand = QVBoxLayout()
        brand.setSpacing(0)
        t1 = QLabel("PROTANKI")
        f1 = T.ui_font(10.5, QFont.Weight.Bold)
        f1.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
        t1.setFont(f1)
        t2 = QLabel("COMMANDER")
        f2 = T.ui_font(7.0, QFont.Weight.DemiBold)
        f2.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3.0)
        t2.setFont(f2)
        t2.setStyleSheet(f"color:{T.TEXT_3};")
        brand.addStretch(1)
        brand.addWidget(t1)
        brand.addWidget(t2)
        brand.addStretch(1)
        h.addLayout(brand)
        h.addSpacing(10)
        self.chip_engine = Chip("Engine")
        self.chip_capture = Chip("Capture")
        self.chip_hotkeys = Chip("Hotkeys")
        for c in (self.chip_engine, self.chip_capture, self.chip_hotkeys):
            h.addWidget(c)
        h.addStretch(1)
        self.exit_btn = ExitAllButton(pretty(self.settings.hotkeys.exit_all).upper())
        self.exit_btn.clicked.connect(self._exit_all)
        h.addWidget(self.exit_btn)
        outer = QWidget()
        ov = QVBoxLayout(outer)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(0)
        ov.addWidget(w)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{T.BORDER};")
        ov.addWidget(line)
        return outer

    def _build_stats(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"background:{T.BG};")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.t_active = StatTile("Active")
        self.t_joins = StatTile("Joins")
        self.t_missed = StatTile("Missed")
        self.t_react = StatTile("Avg reaction", "ms")
        self.t_exit = StatTile("Last exit all", "ms")
        tiles = [self.t_active, self.t_joins, self.t_missed, self.t_react, self.t_exit]
        for i, t in enumerate(tiles):
            if i:
                h.addWidget(vline())
            h.addWidget(t, 1)
        outer = QWidget()
        ov = QVBoxLayout(outer)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(0)
        ov.addWidget(w)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{T.BORDER};")
        ov.addWidget(line)
        return outer

    def _build_toolbar(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(12, 10, 12, 10)
        h.setSpacing(6)
        self.b_start = QPushButton(" Start all")
        self.b_start.setIcon(T.icon("play", "#17130B"))
        self.b_start.setProperty("kind", "primary")
        self.b_start.clicked.connect(lambda: self.engine.submit("START"))
        self.b_pause = QPushButton(" Pause all")
        self.b_pause.setIcon(T.icon("pause"))
        self.b_pause.clicked.connect(lambda: self.engine.submit("PAUSE"))
        self.b_tile = QPushButton(" Tile")
        self.b_tile.setIcon(T.icon("grid"))
        self.b_tile.setToolTip("Arrange every bound client in the grid (Settings → Window grid)")
        self.b_tile.clicked.connect(lambda: self.engine.submit("TILE"))
        self.b_pick = QPushButton(" Pick")
        self.b_pick.setIcon(T.icon("crosshair"))
        self.b_pick.setToolTip("Freeze the screen and pick a button position + colour on any client")
        self.b_pick.clicked.connect(lambda: self._pick(None))
        self.b_add = QPushButton(" Add")
        self.b_add.setToolTip("Add an account")
        self.b_add.setIcon(T.icon("plus"))
        self.b_add.clicked.connect(self._add_account)
        for b in (self.b_start, self.b_pause, self.b_tile, self.b_pick, self.b_add):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            h.addWidget(b)
        h.addStretch(1)
        lbl = caps_label("On exit")
        lbl.setToolTip("What EXIT ALL does")
        h.addWidget(lbl)
        h.addSpacing(4)
        self.seg_exit = Segmented([("leave_battle", "Leave"), ("close_client", "Close"), ("kill", "Kill")])
        self.seg_exit.buttons["leave_battle"].setToolTip("Leave the battle, keep the client in the lobby")
        self.seg_exit.buttons["close_client"].setToolTip("Leave the battle, then close the client")
        self.seg_exit.buttons["kill"].setToolTip("Terminate every client process immediately")
        self.seg_exit.set_value(self.settings.exit_mode)
        self.seg_exit.changed.connect(self._set_exit_mode)
        h.addWidget(self.seg_exit)
        return w

    def _build_statusbar(self) -> None:
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.sb_keys = QLabel("")
        sb.addWidget(self.sb_keys, 1)
        self.metrics = QLabel("")
        self.metrics.setFont(T.mono_font(7.4))
        self.metrics.setToolTip(str(paths.CONFIG_DIR))
        sb.addPermanentWidget(self.metrics)
        self._update_keys_label()

    def _update_keys_label(self) -> None:
        hk = self.settings.hotkeys
        self.sb_keys.setText(f"{pretty(hk.exit_all)}  exit all    ·    {pretty(hk.kill_all)}  kill all    ·    "
                             f"{pretty(hk.pause_all)}  pause all")

    # ------------------------------------------------------------------ refresh

    def refresh(self) -> None:
        if self.isMinimized():
            return
        s = self.snap = self.engine.snapshot()
        accs = s.accounts
        enabled = [a for a in accs if a.enabled]
        live = [a for a in accs if a.state in LIVE]
        in_battle = sum(a.state == "IN_BATTLE" for a in accs)

        if s.paused_all:
            self.chip_engine.set("idle", T.TEXT_3)
        else:
            self.chip_engine.set(f"{s.tick_ms:.0f} ms", T.OK)
        if not s.rendering_ok:
            self.chip_capture.set("NO RENDER", T.DANGER)
        elif s.capture_ms:
            self.chip_capture.set(f"{s.capture_ms:.0f} ms", T.OK)
        else:
            self.chip_capture.set("idle", T.TEXT_3)
        hk_ok = self.ctl.hotkeys_ok()
        self.chip_hotkeys.set("armed" if hk_ok else "conflict", T.OK if hk_ok else "#E2B046")
        self.metrics.setText(f"CPU {s.cpu:.1f}%   RAM {s.ram_mb:.0f} MB   clients {s.clients_ram_mb:.0f} MB   "
                             f"v{__version__}")

        self.t_active.set(f"{len(live)}/{len(enabled)}", f"{in_battle} in battle")
        joins = sum(a.joins for a in accs)
        missed = sum(a.misses for a in accs)
        self.t_joins.set(str(joins), f"{sum(a.clicks for a in accs)} clicks sent")
        self.t_missed.set(str(missed), f"{sum(a.retries for a in accs)} retries",
                          "#F08A8D" if missed else None)
        n = sum(1 for a in accs if a.react_avg is not None)
        if n:
            avg = sum(a.react_avg * a.joins for a in accs if a.react_avg is not None) / max(
                sum(a.joins for a in accs if a.react_avg is not None), 1)
            self.t_react.set(f"≤{avg:.0f}", "screen change → click")
        else:
            self.t_react.set("—", "screen change → click")
        if s.last_exit_ms is not None and s.last_exit_ms != self._last_exit_seen:
            self._last_exit_seen = s.last_exit_ms
            self._exit_flash_until = time.time() + 4
        if s.exit_in_progress:
            self.t_exit.set("…", "in progress", T.DANGER)
        elif s.last_exit_ms is not None:
            hot = time.time() < self._exit_flash_until
            self.t_exit.set(f"{s.last_exit_ms:.0f}", f"{s.last_exit_count} accounts", T.DANGER if hot else None)
        else:
            self.t_exit.set("—", "not triggered yet")
        self.seg_exit.set_value(s.exit_mode)
        n_before = self.table.model_.rowCount()
        self.table.update_rows(accs)
        if len(accs) != n_before:
            self._fit_split()
        self._refresh_inspector()

    def _fit_split(self) -> None:
        """Give the table room for every row (up to 62 % of the height) in the stacked layout."""
        if self.split.orientation() != Qt.Orientation.Vertical:
            return
        total = sum(self.split.sizes()) or self.split.height()
        want = 32 + 40 * max(self.table.model_.rowCount(), 3) + 2
        top = min(want, int(total * 0.62))
        self.split.setSizes([top, max(total - top, 200)])

    def _refresh_inspector(self) -> None:
        cid = self.table.current_id()
        acc = next((a for a in self.snap.accounts if a.id == cid), None)
        self.inspector.set_account(acc)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        want = Qt.Orientation.Horizontal if self.width() >= 1280 else Qt.Orientation.Vertical
        if self.split.orientation() != want:
            self.split.setOrientation(want)
            self.split.setSizes([3, 2] if want == Qt.Orientation.Horizontal else [6, 5])
            self._fit_split()

    # ------------------------------------------------------------------ actions

    def _exit_all(self) -> None:
        self.engine.submit("EXIT_ALL")

    def _set_exit_mode(self, mode: str) -> None:
        s = self.settings.model_copy(update={"exit_mode": mode})
        self._apply_settings(s, quiet=True)

    def _apply_settings(self, s: C.Settings, quiet: bool = False) -> None:
        hotkeys_changed = s.hotkeys != self.settings.hotkeys
        tg_changed = s.telegram != self.settings.telegram
        self.settings = s
        C.save_settings(s)
        self.engine.submit("SET_SETTINGS", arg=s)
        self.inspector.settings_tab.set_exit_mode(s.exit_mode)
        if hotkeys_changed:
            self.ctl.restart_hotkeys(s)
            self.exit_btn.hotkey = pretty(s.hotkeys.exit_all).upper()
            self.exit_btn.update()
            self._update_keys_label()
        if tg_changed:
            self.ctl.restart_telegram(s)
        if not quiet:
            self.statusBar().showMessage("Settings saved and applied", 3000)

    def _row_action(self, acc_id: str, action: str, pos: QPoint) -> None:
        acc = next((a for a in self.snap.accounts if a.id == acc_id), None)
        if acc is None:
            return
        if action == "toggle":
            self.engine.submit("PAUSE" if acc.state in LIVE else "START", acc_id)
        elif action == "focus":
            if acc.hwnd:
                W.force_foreground(acc.hwnd)
        elif action == "more":
            self._row_menu(acc, pos)

    def _row_menu(self, acc, pos: QPoint) -> None:
        m = QMenu(self)
        live = acc.state in LIVE
        m.addAction(T.icon("play"), "Start", lambda: self.engine.submit("START", acc.id)).setEnabled(not live)
        m.addAction(T.icon("pause"), "Pause", lambda: self.engine.submit("PAUSE", acc.id)).setEnabled(live)
        m.addAction(T.icon("eye"), "Focus window", lambda: W.force_foreground(acc.hwnd)).setEnabled(bool(acc.hwnd))
        m.addAction(T.icon("link"), "Bind window…", lambda: self._bind(acc.id))
        m.addAction(T.icon("crosshair"), "Pick point on this client…", lambda: self._pick(acc.id))
        m.addSeparator()
        m.addAction(T.icon("edit"), "Edit account…", lambda: self._edit_account(acc.id))
        m.addAction(T.icon("refresh"), "Reset counters", lambda: self.engine.submit("RESET_STATS", acc.id))
        m.addAction(T.icon("stop"), "Release window (keep client open)",
                    lambda: self.engine.submit("STOP", acc.id)).setEnabled(bool(acc.hwnd))
        m.addSeparator()
        m.addAction(T.icon("trash", "#F08A8D"), "Remove account…", lambda: self._remove_account(acc.id))
        m.exec(pos)

    def _inspector_request(self, action: str, acc_id: str) -> None:
        if action == "focus":
            acc = next((a for a in self.snap.accounts if a.id == acc_id), None)
            if acc and acc.hwnd:
                W.force_foreground(acc.hwnd)
        elif action == "bind":
            self._bind(acc_id)
        elif action == "pick":
            self._pick(acc_id)
        elif action.startswith("seq:"):
            self.engine.submit("RUN_SEQ", acc_id, action[4:])
        elif action.startswith("click:"):
            self.engine.submit("CLICK", acc_id, action[6:])
        elif action.startswith("preset:"):
            self._set_account_preset(acc_id, action[7:])

    # ------------------------------------------------------------------ picker

    def _pick_targets(self) -> list[PickTarget]:
        out = []
        for a in self.snap.accounts:
            if a.hwnd and W.is_window(a.hwnd):
                out.append(PickTarget(a.id, a.name, a.hwnd, W.client_rect_screen(a.hwnd)))
        return out

    def _pick(self, acc_id: str | None) -> None:
        targets = self._pick_targets()
        if not targets:
            QMessageBox.information(self, "Pick point", "No client window is bound yet. Start an account first.")
            return
        self._overlay = PickerOverlay(targets, "point")
        self._overlay.picked.connect(self._on_picked)
        self._overlay.showFullScreen()

    def _on_picked(self, res) -> None:
        acc = next((a for a in self.snap.accounts if a.id == res.target.acc_id), None)
        dlg = PickSaveDialog(self, res, acc.preset if acc else C.list_presets()[0])
        if dlg.exec():
            self.engine.submit("RELOAD")
            self.statusBar().showMessage(f"Saved to preset '{dlg.saved_preset}'", 4000)

    def _bind(self, acc_id: str) -> None:
        mine = int(self.winId())
        own_pid = W.window_pid(mine)
        targets: list[PickTarget] = []

        def cb(h, _):
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) and not W.is_minimized(h) \
                    and W.window_pid(h) != own_pid and W.class_name(h) not in ("Progman", "Shell_TrayWnd"):
                r = W.client_rect_screen(h)
                if r.w > 80 and r.h > 60:
                    targets.append(PickTarget(None, W.title(h)[:40], h, r))
            return True

        win32gui.EnumWindows(cb, None)
        self._overlay = PickerOverlay(targets, "window", "Click the game window to bind it  ·  Esc to cancel")
        self._overlay.window_picked.connect(lambda h: self.engine.submit("BIND", acc_id, h))
        self._overlay.showFullScreen()

    # ------------------------------------------------------------------ accounts

    def _accounts(self) -> list[C.Account]:
        return C.load_accounts()

    def _add_account(self) -> None:
        accs = self._accounts()
        used = {a.slot for a in accs if a.slot is not None}
        nxt = next(i for i in range(64) if i not in used)
        dlg = AccountDialog(self, None, {a.id for a in accs}, nxt)
        if dlg.exec():
            accs.append(dlg.result_account)
            C.save_accounts(accs)
            self.engine.submit("RELOAD")
            self.statusBar().showMessage(f"Added {dlg.result_account.name}", 3000)
            QTimer.singleShot(300, lambda: self.table.select_id(dlg.result_account.id))

    def _edit_account(self, acc_id: str) -> None:
        accs = self._accounts()
        cur = next((a for a in accs if a.id == acc_id), None)
        if cur is None:
            return
        dlg = AccountDialog(self, cur, {a.id for a in accs}, cur.slot or 0)
        if dlg.exec():
            accs = [dlg.result_account if a.id == acc_id else a for a in accs]
            C.save_accounts(accs)
            self.engine.submit("RELOAD")

    def _remove_account(self, acc_id: str) -> None:
        accs = self._accounts()
        cur = next((a for a in accs if a.id == acc_id), None)
        if cur is None:
            return
        if not confirm(self, "Remove account", f"Remove “{cur.name}” from the commander?\n\n"
                       "Its client window is left open.", "Remove", danger=True):
            return
        C.save_accounts([a for a in accs if a.id != acc_id])
        self.engine.submit("STOP", acc_id)
        self.engine.submit("RELOAD")

    def _set_account_preset(self, acc_id: str, preset: str) -> None:
        accs = self._accounts()
        accs = [a.model_copy(update={"preset": preset}) if a.id == acc_id else a for a in accs]
        C.save_accounts(accs)
        self.engine.submit("RELOAD")
        self.statusBar().showMessage(f"Preset '{preset}' assigned", 3000)

    def closeEvent(self, e):
        if any(a.state in LIVE for a in self.snap.accounts):
            if not confirm(self, "Quit", "Automation is running. Quit the commander?\n\n"
                           "Client windows stay open; you can re-attach later.", "Quit"):
                e.ignore()
                return
        e.accept()
