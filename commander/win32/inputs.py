"""Two ways to click a game window.

PostMessageBackend  - drops WM_* messages straight into the window's queue. No focus, no cursor
                      movement, parallel across windows. Primary.
SendInputBackend    - synthesises real mouse/keyboard input. Needs the window in the foreground, so
                      it is serialised behind one global lock. Fallback for engines that ignore
                      posted messages.
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

import win32api
import win32con
import win32gui

from . import windows as W

user32 = ctypes.windll.user32

# ---------------------------------------------------------------- key names

_NAMED_KEYS = {
    "ESC": win32con.VK_ESCAPE, "ESCAPE": win32con.VK_ESCAPE, "ENTER": win32con.VK_RETURN,
    "RETURN": win32con.VK_RETURN, "SPACE": win32con.VK_SPACE, "TAB": win32con.VK_TAB,
    "UP": win32con.VK_UP, "DOWN": win32con.VK_DOWN, "LEFT": win32con.VK_LEFT, "RIGHT": win32con.VK_RIGHT,
    "BACKSPACE": win32con.VK_BACK, "DELETE": win32con.VK_DELETE, "HOME": win32con.VK_HOME,
    "END": win32con.VK_END, "PGUP": win32con.VK_PRIOR, "PGDN": win32con.VK_NEXT,
    "SHIFT": win32con.VK_SHIFT, "CTRL": win32con.VK_CONTROL, "ALT": win32con.VK_MENU,
}
_NAMED_KEYS.update({f"F{i}": win32con.VK_F1 + i - 1 for i in range(1, 13)})


def vk_of(name: str) -> int:
    n = name.strip().upper()
    if n in _NAMED_KEYS:
        return _NAMED_KEYS[n]
    if len(n) == 1 and (n.isalnum()):
        return ord(n)
    raise ValueError(f"unknown key '{name}'")


def _key_lparam(vk: int, up: bool) -> int:
    scan = win32api.MapVirtualKey(vk, 0)
    lp = 1 | (scan << 16)
    if vk in (win32con.VK_LEFT, win32con.VK_RIGHT, win32con.VK_UP, win32con.VK_DOWN,
              win32con.VK_DELETE, win32con.VK_HOME, win32con.VK_END, win32con.VK_PRIOR, win32con.VK_NEXT):
        lp |= 1 << 24                                      # extended key
    if up:
        lp |= (1 << 30) | (1 << 31)
    return lp


# ---------------------------------------------------------------- PostMessage


class PostMessageBackend:
    name = "post"

    def click(self, hwnd: int, x: int, y: int, hold_ms: int = 0) -> None:
        target, (cx, cy) = W.deepest_child(hwnd, x, y)
        lp = win32api.MAKELONG(cx & 0xFFFF, cy & 0xFFFF)
        win32gui.PostMessage(target, win32con.WM_MOUSEMOVE, 0, lp)
        win32gui.PostMessage(target, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp)
        if hold_ms:
            time.sleep(hold_ms / 1000)
        win32gui.PostMessage(target, win32con.WM_LBUTTONUP, 0, lp)

    def key(self, hwnd: int, name: str, hold_ms: int = 0) -> None:
        vk = vk_of(name)
        win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, _key_lparam(vk, False))
        if hold_ms:
            time.sleep(hold_ms / 1000)
        win32gui.PostMessage(hwnd, win32con.WM_KEYUP, vk, _key_lparam(vk, True))

    def close(self, hwnd: int) -> None:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)


# ---------------------------------------------------------------- SendInput

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0001, 0x0002, 0x0004
MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x8000, 0x4000
KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0002, 0x0008
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

_input_lock = threading.Lock()      # one real mouse -> strictly one window at a time


def _send(*items: INPUT) -> None:
    arr = (INPUT * len(items))(*items)
    user32.SendInput(len(items), arr, ctypes.sizeof(INPUT))


def _mouse(flags: int, dx: int = 0, dy: int = 0) -> INPUT:
    inp = INPUT(type=INPUT_MOUSE)
    inp.u.mi = MOUSEINPUT(dx, dy, 0, flags, 0, 0)
    return inp


def _kbd(vk: int, up: bool) -> INPUT:
    inp = INPUT(type=INPUT_KEYBOARD)
    scan = win32api.MapVirtualKey(vk, 0)
    inp.u.ki = KEYBDINPUT(vk, scan, KEYEVENTF_KEYUP if up else 0, 0, 0)
    return inp


def _abs_coords(sx: int, sy: int) -> tuple[int, int]:
    vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)      # SM_X/YVIRTUALSCREEN
    vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    return (int((sx - vx) * 65535 / max(vw - 1, 1)), int((sy - vy) * 65535 / max(vh - 1, 1)))


def _restore_focus(prev_fg: int, game: int) -> None:
    """Give focus back to what the user had - unless that window would cover the game window."""
    if not prev_fg or prev_fg == game or not W.is_window(prev_fg):
        return
    try:
        if W.overlaps(W.window_rect(prev_fg), W.client_rect_screen(game)):
            return
    except win32gui.error:
        return
    W.force_foreground(prev_fg)


class SendInputBackend:
    name = "sendinput"

    def __init__(self, settle_ms: int = 25):
        self.settle_ms = settle_ms

    def click(self, hwnd: int, x: int, y: int, hold_ms: int = 0) -> None:
        sx, sy = W.client_to_screen(hwnd, x, y)
        ax, ay = _abs_coords(sx, sy)
        with _input_lock:
            prev_fg = win32gui.GetForegroundWindow()
            prev_pos = win32api.GetCursorPos()
            W.force_foreground(hwnd)
            time.sleep(self.settle_ms / 1000)
            flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
            _send(_mouse(flags, ax, ay))
            _send(_mouse(MOUSEEVENTF_LEFTDOWN))
            time.sleep(max(hold_ms, 12) / 1000)
            _send(_mouse(MOUSEEVENTF_LEFTUP))
            win32api.SetCursorPos(prev_pos)
            _restore_focus(prev_fg, hwnd)

    def key(self, hwnd: int, name: str, hold_ms: int = 0) -> None:
        vk = vk_of(name)
        with _input_lock:
            prev_fg = win32gui.GetForegroundWindow()
            W.force_foreground(hwnd)
            time.sleep(self.settle_ms / 1000)
            _send(_kbd(vk, False))
            time.sleep(max(hold_ms, 12) / 1000)
            _send(_kbd(vk, True))
            _restore_focus(prev_fg, hwnd)

    def close(self, hwnd: int) -> None:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)


BACKENDS = {"post": PostMessageBackend(), "sendinput": SendInputBackend()}
