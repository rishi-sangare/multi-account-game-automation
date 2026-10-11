"""Application entry point."""
from __future__ import annotations

import logging
import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, __version__
from . import config as C
from . import logbuffer, paths
from .engine import Engine
from .remote.telegram import TelegramBot
from .ui import theme as T
from .ui.main_window import MainWindow
from .win32.hotkeys import HotkeyThread

log = logging.getLogger("commander")


class AppController:
    """Owns the background services that the window may need to restart."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self.hotkeys: HotkeyThread | None = None
        self.telegram: TelegramBot | None = None

    def _on_hotkey(self, action: str) -> None:
        log.info("hotkey: %s", action)
        self.engine.submit({"exit_all": "EXIT_ALL", "kill_all": "KILL_ALL", "pause_all": "PAUSE_ALL"}[action])

    def restart_hotkeys(self, s: C.Settings) -> None:
        if self.hotkeys:
            self.hotkeys.stop()
            self.hotkeys.join(1)
        self.hotkeys = HotkeyThread(s.hotkeys.model_dump(), self._on_hotkey)
        self.hotkeys.start()
        self.hotkeys.wait_ready()

    def hotkeys_ok(self) -> bool:
        return bool(self.hotkeys and self.hotkeys.registered and all(self.hotkeys.registered.values()))

    def restart_telegram(self, s: C.Settings) -> None:
        if self.telegram:
            self.telegram.stop()
            self.telegram = None
        if s.telegram.enabled and s.telegram.token and s.telegram.allowed_user_id:
            self.telegram = TelegramBot(s.telegram.token, s.telegram.allowed_user_id, self.engine)
            self.telegram.start()

    def shutdown(self) -> None:
        if self.hotkeys:
            self.hotkeys.stop()
        if self.telegram:
            self.telegram.stop()
        self.engine.shutdown()
        self.engine.join(2)


def _palette() -> QPalette:
    pal = QPalette()
    for role, c in ((QPalette.ColorRole.Window, T.BG), (QPalette.ColorRole.Base, T.SURFACE),
                    (QPalette.ColorRole.AlternateBase, T.SURFACE_2), (QPalette.ColorRole.Text, T.TEXT),
                    (QPalette.ColorRole.WindowText, T.TEXT), (QPalette.ColorRole.Button, T.SURFACE_2),
                    (QPalette.ColorRole.ButtonText, T.TEXT), (QPalette.ColorRole.Highlight, T.ACCENT_DIM),
                    (QPalette.ColorRole.HighlightedText, T.TEXT), (QPalette.ColorRole.ToolTipBase, T.SURFACE_3),
                    (QPalette.ColorRole.ToolTipText, T.TEXT), (QPalette.ColorRole.PlaceholderText, T.TEXT_3)):
        pal.setColor(role, QColor(c))
    return pal


def _place_window(win: MainWindow, s: C.Settings) -> None:
    """Put the commander beside the client grid so nothing overlaps a game window."""
    screen = QGuiApplication.primaryScreen()
    avail = screen.availableGeometry()
    dpr = screen.devicePixelRatio()
    lay = s.layout
    grid_w = (lay.origin[0] + lay.cols * (lay.client_w + 2 + lay.gap)) / dpr
    x = int(avail.left() + grid_w + 6)
    w = avail.right() - x
    if w >= 620:
        win.setGeometry(x, avail.top() + 1, w, avail.height() - 32)
    else:
        win.resize(min(1180, avail.width() - 80), min(860, avail.height() - 80))


def main() -> int:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)                      # Qt makes the process per-monitor DPI aware (v2)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    T.init_fonts()
    app.setFont(T.ui_font(9))
    app.setPalette(_palette())
    app.setStyleSheet(T.stylesheet())

    paths.ensure_dirs()
    logbuffer.setup()
    log.info("%s v%s starting · root %s", APP_NAME, __version__, paths.ROOT)
    try:
        settings = C.load_settings()
        accounts = C.load_accounts()
    except C.ConfigError as e:
        QMessageBox.critical(None, APP_NAME, f"Configuration error:\n\n{e}")
        return 1

    engine = Engine(settings, accounts)
    engine.start()
    ctl = AppController(engine)
    ctl.restart_hotkeys(settings)
    ctl.restart_telegram(settings)

    win = MainWindow(engine, settings, ctl)
    _place_window(win, settings)
    win.show()
    app.aboutToQuit.connect(ctl.shutdown)
    if "--autostart" in sys.argv:
        engine.submit("START")
    if "--demo" in sys.argv:
        from .demo import Director
        win._director = Director(win, engine, record="--no-record" not in sys.argv)
        win._director.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
