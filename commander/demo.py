"""Demo director: a scripted, screen-recorded walkthrough of the app (for the Loom video).

    python run.py --demo

It hides the desktop behind a plain backdrop, starts an ffmpeg screen recording, then drives the
real UI with a real cursor (smooth moves, clicks, hotkeys) scene by scene. Each scene is held long
enough for its narration line, and every scene start is logged so that make_demo_video.py can build
captions and a timed transcript. Hands off the mouse and keyboard while it runs (~4.5 minutes).
"""
from __future__ import annotations

import ctypes
import json
import logging
import math
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import psutil
import win32api
from PySide6.QtCore import QEasingCurve, QObject, QPoint, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QWidget

from . import paths
from .ui import theme as T
from .win32 import windows as W
from .win32.inputs import INPUT, INPUT_KEYBOARD, KEYBDINPUT, _send

log = logging.getLogger("demo")
user32 = ctypes.windll.user32
WORDS_PER_SEC = 2.45


# --------------------------------------------------------------------------- script


@dataclass
class Scene:
    id: str
    caption: str
    narration: str
    min_s: float = 0.0

    @property
    def duration(self) -> float:
        return max(self.min_s, len(self.narration.split()) / WORDS_PER_SEC + 0.9)


SCENES = [
    Scene("intro", "ProTanki Commander · working prototype built for your job post",
          "Hi! I read your job post, and before applying I built a working prototype of the exact tool you "
          "described. This is ProTanki Commander, running eight accounts on one Windows machine."),
    Scene("start", "Start all → every client launches and tiles into a fixed grid",
          "One click on Start all launches every account. Each client gets its own window, and the tool arranges "
          "them in a fixed grid, so every click position always stays exactly the same.", 12),
    Scene("watch", "Watching every client ~22× a second · PLAY turns green → instant click",
          "Now it watches all eight clients about twenty times a second. The moment the Play button turns green, "
          "it clicks. In the background, without touching my mouse, and on all accounts at the same time."),
    Scene("verify", "Every click is verified, retried if needed, and counted",
          "Every click is verified. If the game doesn't react, it retries, and if the game ignores background "
          "clicks, it automatically switches to real mouse input. You can see joins, misses and reaction time "
          "for every single account."),
    Scene("reaction", "Measured inside the client: green on screen → click arrived",
          "These are test clients I built that look and behave like the ProTanki lobby. They measure the real "
          "reaction time, from the button turning green to the click arriving. It is usually well under a "
          "hundred milliseconds."),
    Scene("live", "Live view: exactly what the engine sees for the selected account",
          "This live view shows exactly what the engine sees for the selected account. Each marker is a trigger "
          "point, and green means it matches right now."),
    Scene("pick", "Pick: freeze the screen → click a button → saved to the preset",
          "Setting click positions is easy. Pick freezes the screen and gives you a magnifier. Click any button, "
          "and it is saved as a point, together with its colour as a trigger. You can also drag a box to save an "
          "image template.", 17),
    Scene("preset", "Presets are plain data · positions stored as ratios of the window",
          "Presets are just data. Positions are stored as ratios of the window, so they work at any window size, "
          "and if the game updates, you simply re-pick a button in a minute."),
    Scene("settings", "Scan rate · retries · exit mode · window grid · hotkeys · Telegram",
          "In settings you control the scan speed, retries, the window grid, the global hotkeys, and an optional "
          "Telegram remote."),
    Scene("exit", "EXIT ALL · Ctrl+Alt+X works from any window",
          "Now the most important part: exit all. I press Control, Alt, X, which works from any window, and every "
          "account leaves the battle at the same moment. All of them were out in a fraction of a second.", 15),
    Scene("resume", "Resume with one key",
          "Starting again is one key, and they go straight back to watching for the next battle.", 9),
    Scene("crash", "Client crashed → watchdog relaunches it → it rejoins by itself",
          "If a client crashes or gets closed, the watchdog notices, relaunches it, puts it back in its slot, and "
          "it carries on by itself. No babysitting needed.", 13),
    Scene("modes", "Exit modes: leave battle · close client · kill",
          "Exit all has three modes: leave the battle, close the clients, or kill them instantly. Here it leaves "
          "the battle and then closes every client.", 11),
    Scene("restart", "Start all brings everything back",
          "And Start all brings everything back.", 9),
    Scene("accounts", "Add or remove accounts in seconds · launch or attach",
          "Adding or removing an account takes a few seconds. It can launch the game for you, or attach to a "
          "client you already logged into.", 11),
    Scene("log", "Every click, retry and exit is logged",
          "Everything is logged, so you can always see what happened, and when.", 6),
    Scene("resources", "One process · ~20 % of one CPU core · ~180 MB for 8 accounts",
          "It's one lightweight process. About twenty percent of a single CPU core and around a hundred and "
          "eighty megabytes for eight accounts, so it won't slow your VPS down. It ships as one folder with an "
          "exe. Nothing to install."),
    Scene("vps", "VPS-ready: keeps working when Remote Desktop is minimised or closed",
          "I've also handled the classic VPS problem. When Remote Desktop is minimised or closed, Windows stops "
          "drawing the screen. The fixes are included, and the tool warns you if it ever happens."),
    Scene("outro", "Ready to adapt it to your buttons and your VPS",
          "Once we connect, I'll tune it to your exact buttons and your VPS, deliver the exe with a setup guide, "
          "and run a live demo on your server. I've added a few quick questions in my proposal. Thanks for "
          "watching!"),
]


# --------------------------------------------------------------------------- visual helpers


class Backdrop(QWidget):
    """Plain full-screen backdrop so the recording shows only the tool and its clients."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(QGuiApplication.primaryScreen().geometry())
        self.setStyleSheet(f"background:{T.BG};")

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#08090B"))


class TitleCard(QWidget):
    def __init__(self, title: str, lines: list[str]):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.title, self.lines = title, lines
        self.resize(820, 300)
        geo = QGuiApplication.primaryScreen().geometry()
        self.move(geo.center() - self.rect().center())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(T.BORDER_2), 1))
        p.setBrush(QColor(T.SURFACE))
        p.drawRoundedRect(r, 12, 12)
        p.fillRect(QRectF(r.left() + 1, r.top() + 1, 4, r.height() - 2), QColor(T.ACCENT))
        p.drawPixmap(44, 46, T.emblem_pixmap(56))
        f = T.ui_font(22, QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        p.setFont(f)
        p.setPen(QColor(T.TEXT))
        p.drawText(QRectF(120, 40, r.width() - 140, 40), Qt.AlignmentFlag.AlignVCenter, self.title)
        p.setFont(T.caps_font(8.5))
        p.setPen(QColor(T.ACCENT))
        p.drawText(QRectF(122, 80, r.width() - 140, 24), Qt.AlignmentFlag.AlignVCenter, "prototype · live demo")
        p.setFont(T.ui_font(12))
        y = 140
        for i, ln in enumerate(self.lines):
            p.setPen(QColor(T.TEXT if i == 0 else T.TEXT_2))
            p.drawText(QRectF(48, y, r.width() - 90, 30), Qt.AlignmentFlag.AlignVCenter, ln)
            y += 36

    def fade(self, show: bool, ms: int = 450) -> None:
        if show:
            self.setWindowOpacity(0.0)
            self.show()
        a = QPropertyAnimation(self, b"windowOpacity", self)
        a.setDuration(ms)
        a.setStartValue(0.0 if show else 1.0)
        a.setEndValue(1.0 if show else 0.0)
        a.setEasingCurve(QEasingCurve.Type.InOutQuad)
        if not show:
            a.finished.connect(self.hide)
        a.start()
        self._anim = a


class Ripple(QWidget):
    """Expanding ring at a click position (input-transparent, top-most)."""

    def __init__(self, gx: int, gy: int):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(70, 70)
        self.move(gx - 35, gy - 35)
        self.t = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)
        self.show()

    def _tick(self):
        self.t += 16 / 420
        if self.t >= 1:
            self.close()
            return
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        e = 1 - (1 - self.t) ** 3
        c = QColor(T.ACCENT)
        c.setAlphaF(0.9 * (1 - self.t))
        p.setPen(QPen(c, 3))
        rad = 8 + 24 * e
        p.drawEllipse(QRectF(35 - rad, 35 - rad, 2 * rad, 2 * rad))


class KeyBadge(QWidget):
    """Keystroke visualiser shown in the empty area under the client grid."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.keys: list[str] = []
        self.resize(420, 64)

    def show_keys(self, keys: list[str], at: QPoint) -> None:
        self.keys = keys
        self.move(at)
        self.show()
        self.raise_()
        self.update()
        QTimer.singleShot(1600, self.hide)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = T.mono_font(13, QFont.Weight.DemiBold)
        p.setFont(f)
        fm = p.fontMetrics()
        x = 4
        for i, k in enumerate(self.keys):
            w = fm.horizontalAdvance(k) + 30
            r = QRectF(x, 8, w, 46)
            p.setPen(QPen(QColor(T.BORDER_2), 1.5))
            p.setBrush(QColor(T.SURFACE_3))
            p.drawRoundedRect(r, 7, 7)
            p.setPen(QColor(T.TEXT))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, k)
            x += w + 10
            if i < len(self.keys) - 1:
                p.setPen(QColor(T.TEXT_3))
                p.drawText(QRectF(x - 6, 8, 14, 46), Qt.AlignmentFlag.AlignCenter, "+")
                x += 10


# --------------------------------------------------------------------------- director


class Director(QObject):
    def __init__(self, win, engine, out_dir: Path | None = None, record: bool = True, quit_after: bool = True):
        super().__init__()
        self.win = win
        self.engine = engine
        self.record = record
        self.quit_after = quit_after
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.out = out_dir or (paths.ROOT / "recordings" / stamp)
        self.out.mkdir(parents=True, exist_ok=True)
        self.dpr = QGuiApplication.primaryScreen().devicePixelRatio()
        self.ffmpeg: subprocess.Popen | None = None
        self.t0 = 0.0
        self.marks: list[dict] = []
        self.gen = None
        self.backdrop = Backdrop()
        self.keys = KeyBadge()

    # -- coroutine plumbing: the script yields delays in ms, QTimer resumes it
    def start(self) -> None:
        self.gen = self._script()
        QTimer.singleShot(300, self._step)

    def _step(self) -> None:
        try:
            delay = next(self.gen)
        except StopIteration:
            return
        except Exception:
            log.exception("demo script failed")
            self._stop_recording()
            return
        QTimer.singleShot(max(0, int(delay or 0)), self._step)

    # -- primitives (generators)
    def wait(self, s: float):
        end = time.perf_counter() + s
        while time.perf_counter() < end:
            yield min(100, (end - time.perf_counter()) * 1000)

    def wait_until(self, cond, timeout: float):
        end = time.perf_counter() + timeout
        while time.perf_counter() < end and not cond(self.engine.snapshot()):
            yield 150

    def phys(self, widget: QWidget, fx: float = 0.5, fy: float = 0.5) -> tuple[int, int]:
        g = widget.mapToGlobal(QPoint(int(widget.width() * fx), int(widget.height() * fy)))
        return round(g.x() * self.dpr), round(g.y() * self.dpr)

    def move(self, x: int, y: int, ms: int = 650):
        sx, sy = win32api.GetCursorPos()
        n = max(1, ms // 16)
        for i in range(1, n + 1):
            t = i / n
            e = 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2
            # slight arc so it reads as a hand, not a robot
            arc = math.sin(math.pi * t) * min(60, math.hypot(x - sx, y - sy) * 0.08)
            user32.SetCursorPos(int(sx + (x - sx) * e), int(sy + (y - sy) * e - arc))
            yield 16

    def move_to(self, widget: QWidget, fx: float = 0.5, fy: float = 0.5, ms: int = 650):
        yield from self.move(*self.phys(widget, fx, fy), ms=ms)

    def click(self):
        x, y = win32api.GetCursorPos()
        user32.mouse_event(2, 0, 0, 0, 0)
        yield 40
        user32.mouse_event(4, 0, 0, 0, 0)
        Ripple(round(x / self.dpr), round(y / self.dpr))
        yield 120

    def press(self, combo: str, show: bool = True):
        names = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "esc": 0x1B, "f5": 0x74, "f6": 0x75}
        parts = combo.lower().split("+")
        codes = [names[p] if p in names else ord(p.upper()) for p in parts]
        if show:
            self.keys.show_keys([p.capitalize() if len(p) > 1 else p.upper() for p in parts],
                                QPoint(32, int(870 / self.dpr)))
        for c in codes:
            user32.keybd_event(c, 0, 0, 0)
            yield 45
        for c in reversed(codes):
            user32.keybd_event(c, 0, 2, 0)
            yield 30

    def type_text(self, text: str):
        for ch in text:
            dn = INPUT(type=INPUT_KEYBOARD)
            dn.u.ki = KEYBDINPUT(0, ord(ch), 0x0004, 0, 0)
            up = INPUT(type=INPUT_KEYBOARD)
            up.u.ki = KEYBDINPUT(0, ord(ch), 0x0004 | 0x0002, 0, 0)
            _send(dn, up)
            yield 85

    def mock_rect(self, name: str) -> W.Rect | None:
        a = next((x for x in self.engine.snapshot().accounts if x.name == name), None)
        if a and a.hwnd and W.is_window(a.hwnd):
            return W.client_rect_screen(a.hwnd)
        return None

    def move_mock(self, name: str, fx: float, fy: float, ms: int = 700):
        r = self.mock_rect(name)
        if r:
            yield from self.move(r.left + int(r.w * fx), r.top + int(r.h * fy), ms)

    def scene(self, sc: Scene):
        stray = self.open_dialog()
        if stray is not None:                                   # a modal would swallow every later click
            log.warning("demo: closing stray dialog before scene %s", sc.id)
            stray.reject()
        t = time.perf_counter() - self.t0
        self.marks.append({"id": sc.id, "t": round(t, 2), "caption": sc.caption, "narration": sc.narration,
                           "duration": round(sc.duration, 2)})
        log.info("demo scene %-9s at %6.2fs", sc.id, t)
        self._scene_end = time.perf_counter() + sc.duration
        yield 0

    def hold(self):
        yield from self.wait(max(0.0, self._scene_end - time.perf_counter()))

    @staticmethod
    def open_dialog() -> QDialog | None:
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QDialog) and w.isVisible():
                return w
        return None

    def cancel_dialog(self):
        dlg = self.open_dialog()
        if dlg is None:
            return
        btn = next((b for b in dlg.findChildren(QPushButton) if b.text() == "Cancel"), None)
        if btn:
            yield from self.move_to(btn, ms=500)
            yield from self.click()
            yield 400
        if dlg.isVisible():                                     # click missed: never leave a modal open
            dlg.reject()
            yield 200

    # -- recording
    def _start_recording(self) -> None:
        if not self.record:
            self.t0 = time.perf_counter()
            return
        try:
            import imageio_ffmpeg
            ff = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            ff = "ffmpeg"
        geo = QGuiApplication.primaryScreen().geometry()
        w = round(geo.width() * self.dpr)
        avail = QGuiApplication.primaryScreen().availableGeometry()
        h = round(avail.height() * self.dpr) // 2 * 2
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
               "-i", "ddagrab=output_idx=0:framerate=30:draw_mouse=1",
               "-vf", f"hwdownload,format=bgra,crop={w}:{h}:0:0,format=yuv420p",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(self.out / "raw.mp4")]
        self.ffmpeg = subprocess.Popen(cmd, stdin=subprocess.PIPE, creationflags=0x08000000)
        self.t0 = time.perf_counter() + 0.45                      # ddagrab start-up latency
        log.info("recording to %s", self.out / "raw.mp4")

    def _stop_recording(self) -> None:
        if self.ffmpeg and self.ffmpeg.poll() is None:
            try:
                self.ffmpeg.stdin.write(b"q")
                self.ffmpeg.stdin.flush()
                self.ffmpeg.wait(20)
            except Exception:
                self.ffmpeg.kill()
        (self.out / "scenes.json").write_text(json.dumps(
            {"total": round(time.perf_counter() - self.t0, 2), "scenes": self.marks}, indent=2), encoding="utf-8")
        log.info("demo finished · %s", self.out)

    # -- the walkthrough
    def _script(self):
        win, eng = self.win, self.engine
        S = {s.id: s for s in SCENES}
        eng.submit("KILL_ALL")
        eng.submit("RESET_STATS")
        yield 1500
        # Backdrop goes ABOVE every other app (hiding the desktop), the Commander above the backdrop;
        # client windows launched later open on top of both.
        self.backdrop.show()
        self.backdrop.raise_()
        yield 300
        win.showNormal()
        win.raise_()
        win.activateWindow()
        W.force_foreground(int(win.winId()))
        yield 300
        win.inspector.setCurrentIndex(0)
        yield from self.move(1450, 640, 10)
        yield 800
        card = TitleCard("ProTanki Commander", ["Multi-account controller · built for your job post",
                                                "8 accounts · instant join · one-key exit all"])
        self._start_recording()
        yield 600

        # 1 intro
        yield from self.scene(S["intro"])
        card.fade(True)
        yield from self.wait(S["intro"].duration - 1.0)
        card.fade(False)
        yield from self.hold()

        # 2 start all
        yield from self.scene(S["start"])
        yield from self.move_to(win.b_start)
        yield from self.click()
        yield from self.hold()

        # 3 watching - point at the joins tile
        yield from self.scene(S["watch"])
        yield from self.move_to(win.t_joins, 0.25, 0.55, 800)
        yield from self.wait(4)
        yield from self.move_mock("Kestrel", 0.85, 0.9, 900)
        yield from self.hold()

        # 4 verify - missed + reaction tiles
        yield from self.scene(S["verify"])
        yield from self.move_to(win.t_missed, 0.25, 0.55)
        yield from self.wait(4)
        yield from self.move_to(win.t_react, 0.3, 0.55)
        yield from self.wait(4)
        yield from self.move_to(win.table.viewport(), 0.62, 0.35, 700)
        yield from self.hold()

        # 5 reaction measured in a mock lobby
        yield from self.scene(S["reaction"])
        yield from self.wait_until(lambda s: any(a.state == "WATCHING" for a in s.accounts), 8)
        snap = eng.snapshot()
        watching = next((a.name for a in snap.accounts if a.state == "WATCHING"), "IronVolk")
        yield from self.move_mock(watching, 0.12, 0.93, 900)
        yield from self.hold()

        # 6 live view: select a row
        yield from self.scene(S["live"])
        idx = win.table.model_.index(2, 1)
        rect = win.table.visualRect(idx)
        g = win.table.viewport().mapToGlobal(rect.center())
        yield from self.move(round(g.x() * self.dpr), round(g.y() * self.dpr), 700)
        yield from self.click()
        win.inspector.setCurrentIndex(0)
        yield from self.wait(1.5)
        yield from self.move_to(win.inspector.live.preview, 0.5, 0.45, 800)
        yield from self.hold()

        # 7 picker
        yield from self.scene(S["pick"])
        yield from self.move_to(win.b_pick)
        yield from self.click()
        yield from self.wait(1.2)
        snap = eng.snapshot()
        lobby = next((a.name for a in snap.accounts if a.state == "WATCHING"), "Sandstorm")
        yield from self.move_mock(lobby, 0.84, 0.9, 1100)
        yield from self.wait(2.5)
        yield from self.move_mock(lobby, 0.42, 0.04, 900)
        yield from self.wait(1.5)
        yield from self.click()
        yield from self.wait(3.5)
        yield from self.cancel_dialog()
        yield from self.hold()

        # 8 preset tab
        yield from self.scene(S["preset"])
        tabs = win.inspector.tabBar()
        yield from self.move(*self._tab_pos(tabs, 1), ms=600)
        yield from self.click()
        yield from self.wait(1.2)
        yield from self.move_to(win.inspector.preset.points, 0.55, 0.3, 700)
        yield from self.hold()

        # 9 settings tab
        yield from self.scene(S["settings"])
        yield from self.move(*self._tab_pos(tabs, 2), ms=600)
        yield from self.click()
        yield from self.hold()

        # 10 exit all via hotkey
        yield from self.move(*self._tab_pos(tabs, 0), ms=500)
        yield from self.click()
        yield from self.wait_until(lambda s: sum(a.state == "IN_BATTLE" for a in s.accounts) >= 5, 30)
        yield from self.scene(S["exit"])
        yield from self.move_to(win.exit_btn, 0.5, 0.5, 700)
        yield from self.wait(3.6)
        yield from self.press("ctrl+alt+x")
        yield from self.wait(2.0)
        yield from self.move_to(win.t_exit, 0.25, 0.55, 700)
        yield from self.hold()

        # 11 resume
        yield from self.scene(S["resume"])
        W.force_foreground(int(win.winId()))
        yield from self.move_to(win.b_start, ms=600)
        yield from self.press("f5")
        yield 600
        if not any(a.state in ("LAUNCHING", "CONNECTING", "WATCHING") for a in eng.snapshot().accounts):
            eng.submit("START")                                 # keystroke lost: same action, guaranteed
        yield from self.hold()

        # 12 crash + relaunch
        yield from self.scene(S["crash"])
        yield from self.move_mock("Tundra", 0.5, 0.5, 800)
        yield from self.wait(1.2)
        a = next((x for x in eng.snapshot().accounts if x.name == "Tundra"), None)
        if a and a.pid:
            for p in W.process_tree(a.pid):
                try:
                    psutil.Process(p).kill()
                except psutil.Error:
                    pass
        yield from self.wait(2.5)
        yield from self.move_to(win.table.viewport(), 0.6, 0.85, 700)
        yield from self.hold()

        # 13 exit modes: close
        yield from self.scene(S["modes"])
        yield from self.move_to(win.seg_exit.buttons["close_client"], ms=700)
        yield from self.click()
        yield from self.wait(1.5)
        yield from self.move_to(win.exit_btn, ms=600)
        yield from self.click()
        yield from self.hold()

        # 14 restart
        yield from self.scene(S["restart"])
        yield from self.move_to(win.seg_exit.buttons["leave_battle"], ms=600)
        yield from self.click()
        yield from self.move_to(win.b_start, ms=600)
        yield from self.click()
        yield from self.hold()

        # 15 add account dialog
        yield from self.scene(S["accounts"])
        yield from self.move_to(win.b_add, ms=600)
        yield from self.click()
        yield from self.wait(1.0)
        yield from self.type_text("Tanker09")
        yield from self.wait(3.0)
        yield from self.cancel_dialog()
        yield from self.hold()

        # 16 log tab
        yield from self.scene(S["log"])
        yield from self.move(*self._tab_pos(tabs, 3), ms=600)
        yield from self.click()
        yield from self.hold()

        # 17 resources
        yield from self.scene(S["resources"])
        yield from self.move_to(win.metrics, 0.5, 0.5, 900)
        yield from self.hold()

        # 18 vps
        yield from self.scene(S["vps"])
        yield from self.move_to(win.chip_capture, 0.6, 0.5, 900)
        yield from self.hold()

        # 19 outro
        yield from self.scene(S["outro"])
        eng.submit("PAUSE")
        end = TitleCard("Ready when you are", ["I'll tune it to your exact buttons and VPS",
                                               "exe + setup guide + live demo on your server · 3–4 days"])
        end.fade(True)
        yield from self.hold()
        yield 400
        self._stop_recording()
        end.hide()
        self.backdrop.hide()
        win.inspector.setCurrentIndex(0)
        if self.quit_after:
            eng.submit("KILL_ALL")
            yield 1500
            QApplication.quit()

    def _tab_pos(self, tabs, i: int) -> tuple[int, int]:
        r = tabs.tabRect(i)
        g = tabs.mapToGlobal(r.center())
        return round(g.x() * self.dpr), round(g.y() * self.dpr)
