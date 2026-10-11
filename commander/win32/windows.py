"""Window discovery, geometry and coordinate conversion (all values in physical pixels)."""
from __future__ import annotations

import ctypes
import re
from ctypes import wintypes
from dataclasses import dataclass

import psutil
import win32con
import win32gui
import win32process

user32 = ctypes.windll.user32

user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.restype = wintypes.UINT
user32.AdjustWindowRectExForDpi.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD, wintypes.BOOL,
                                            wintypes.DWORD, wintypes.UINT]
user32.AdjustWindowRectExForDpi.restype = wintypes.BOOL


def set_dpi_aware() -> None:
    """Per-monitor v2 so every coordinate we see is a real pixel. Safe to call twice."""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            user32.SetProcessDPIAware()


@dataclass
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def w(self) -> int:
        return self.right - self.left

    @property
    def h(self) -> int:
        return self.bottom - self.top

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


def is_window(hwnd: int | None) -> bool:
    return bool(hwnd) and bool(win32gui.IsWindow(hwnd))


def window_pid(hwnd: int) -> int:
    return win32process.GetWindowThreadProcessId(hwnd)[1]


def title(hwnd: int) -> str:
    try:
        return win32gui.GetWindowText(hwnd)
    except win32gui.error:
        return ""


def class_name(hwnd: int) -> str:
    try:
        return win32gui.GetClassName(hwnd)
    except win32gui.error:
        return ""


def process_tree(pid: int) -> set[int]:
    """pid plus all of its live descendants (launchers often spawn the real client)."""
    pids = {pid}
    try:
        pids |= {c.pid for c in psutil.Process(pid).children(recursive=True)}
    except psutil.Error:
        pass
    return pids


def top_windows_for_pids(pids: set[int]) -> list[int]:
    found: list[int] = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) and window_pid(h) in pids:
            if win32gui.GetWindow(h, win32con.GW_OWNER) == 0:      # skip owned popups/tooltips
                found.append(h)
        return True

    win32gui.EnumWindows(cb, None)
    return found


def find_by_title(pattern: str, exclude: set[int] | None = None) -> int | None:
    rx = re.compile(pattern)
    exclude = exclude or set()
    hit: list[int] = []

    def cb(h, _):
        if h not in exclude and win32gui.IsWindowVisible(h) and rx.search(win32gui.GetWindowText(h) or ""):
            hit.append(h)
        return True

    win32gui.EnumWindows(cb, None)
    return hit[0] if hit else None


def root_window(hwnd: int) -> int:
    return user32.GetAncestor(hwnd, 2)  # GA_ROOT


def window_at(x: int, y: int) -> int:
    return win32gui.WindowFromPoint((x, y))


def client_rect_screen(hwnd: int) -> Rect:
    """Client area in screen coordinates."""
    l, t, r, b = win32gui.GetClientRect(hwnd)
    sx, sy = win32gui.ClientToScreen(hwnd, (0, 0))
    return Rect(sx, sy, sx + (r - l), sy + (b - t))


def client_size(hwnd: int) -> tuple[int, int]:
    l, t, r, b = win32gui.GetClientRect(hwnd)
    return r - l, b - t


def client_to_screen(hwnd: int, x: int, y: int) -> tuple[int, int]:
    return win32gui.ClientToScreen(hwnd, (x, y))


def screen_to_client(hwnd: int, x: int, y: int) -> tuple[int, int]:
    return win32gui.ScreenToClient(hwnd, (x, y))


def is_minimized(hwnd: int) -> bool:
    return bool(win32gui.IsIconic(hwnd))


def restore_no_activate(hwnd: int) -> None:
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)


def set_title(hwnd: int, text: str) -> None:
    try:
        win32gui.SetWindowText(hwnd, text)
    except win32gui.error:
        pass


def _frame_rect(hwnd: int, cw: int, ch: int) -> wintypes.RECT:
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    rc = wintypes.RECT(0, 0, cw, ch)
    dpi = user32.GetDpiForWindow(hwnd) or 96
    user32.AdjustWindowRectExForDpi(ctypes.byref(rc), style & 0xFFFFFFFF, False, exstyle & 0xFFFFFFFF, dpi)
    return rc


def caption_height(hwnd: int) -> int:
    """Height of title bar + top border above the client area."""
    return -_frame_rect(hwnd, 100, 100).top


def place_client(hwnd: int, cx: int, cy: int, cw: int, ch: int) -> None:
    """Move/resize so the *client* area is exactly cw x ch with its top-left at screen (cx, cy)."""
    if is_minimized(hwnd):
        restore_no_activate(hwnd)
    rc = _frame_rect(hwnd, cw, ch)
    win32gui.SetWindowPos(hwnd, 0, cx + rc.left, cy + rc.top, rc.right - rc.left, rc.bottom - rc.top,
                          win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)


def deepest_child(top: int, x: int, y: int) -> tuple[int, tuple[int, int]]:
    """Walk to the innermost visible child at client point (x, y) of `top`.
    Messages must go to that child, in that child's client coordinates."""
    flags = win32con.CWP_SKIPINVISIBLE | win32con.CWP_SKIPDISABLED | win32con.CWP_SKIPTRANSPARENT
    hwnd, pt = top, (x, y)
    for _ in range(16):
        try:
            child = win32gui.ChildWindowFromPointEx(hwnd, pt, flags)
        except win32gui.error:
            break
        if not child or child == hwnd:
            break
        sx, sy = win32gui.ClientToScreen(hwnd, pt)
        pt = win32gui.ScreenToClient(child, (sx, sy))
        hwnd = child
    return hwnd, pt


def window_rect(hwnd: int) -> Rect:
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    return Rect(l, t, r, b)


def overlaps(a: Rect, b: Rect) -> bool:
    return a.left < b.right and b.left < a.right and a.top < b.bottom and b.top < a.bottom


def is_covered(hwnd: int) -> bool:
    """True when another window sits on top of part of this client area (screen capture would see it)."""
    r = client_rect_screen(hwnd)
    if r.w <= 2 or r.h <= 2:
        return False
    for fx, fy in ((0.5, 0.5), (0.1, 0.1), (0.9, 0.1), (0.1, 0.9), (0.9, 0.9)):
        x, y = r.left + int(r.w * fx), r.top + int(r.h * fy)
        try:
            if root_window(window_at(x, y)) != hwnd:
                return True
        except win32gui.error:
            return False
    return False


def raise_no_activate(hwnd: int) -> None:
    """Bring a window to the top of the z-order without stealing keyboard focus."""
    win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, 0, 0, 0, 0,
                          win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)


def force_foreground(hwnd: int) -> bool:
    """SetForegroundWindow with the AttachThreadInput workaround for the foreground lock."""
    if not is_window(hwnd):
        return False
    fg = win32gui.GetForegroundWindow()
    if fg == hwnd:
        return True
    cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
    fg_tid = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
    attached = False
    try:
        if fg_tid and fg_tid != cur_tid:
            attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
        if is_minimized(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        ok = bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(cur_tid, fg_tid, False)
    return ok


def describe_tree(hwnd: int, depth: int = 0, out: list[str] | None = None) -> list[str]:
    """Human-readable child tree (used by the Phase-0 spike tools)."""
    out = [] if out is None else out
    r = win32gui.GetWindowRect(hwnd)
    out.append(f"{'  ' * depth}0x{hwnd:08X}  class={class_name(hwnd)!r}  title={title(hwnd)!r}  rect={r}")
    children: list[int] = []

    def cb(h, _):
        if win32gui.GetParent(h) == hwnd:
            children.append(h)
        return True

    try:
        win32gui.EnumChildWindows(hwnd, cb, None)
    except win32gui.error:
        pass
    for c in children:
        describe_tree(c, depth + 1, out)
    return out
