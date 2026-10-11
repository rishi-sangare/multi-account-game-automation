"""Pixel capture.

Primary path: GDI screen grab (mss) of small rectangles. Windows are tiled with no overlap, so the
screen *is* each window's content. Fallback: PrintWindow(PW_RENDERFULLCONTENT) per window, which
also works for windows that are covered by something else.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

import mss
import numpy as np
import win32gui
import win32ui

from .windows import Rect

_tls = threading.local()


def _sct() -> mss.base.MSSBase:
    # mss keeps per-thread GDI handles: one instance per thread.
    s = getattr(_tls, "sct", None)
    if s is None:
        s = _tls.sct = (mss.MSS() if hasattr(mss, "MSS") else mss.mss())
    return s


def grab(rect: Rect) -> np.ndarray:
    """BGRA uint8 array (h, w, 4) of a screen rectangle."""
    shot = _sct().grab({"left": rect.left, "top": rect.top, "width": max(rect.w, 1), "height": max(rect.h, 1)})
    return np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)


def grab_full() -> tuple[np.ndarray, int, int]:
    """Whole virtual desktop. Returns (BGRA array, left, top)."""
    mon = _sct().monitors[0]
    shot = _sct().grab(mon)
    arr = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
    return arr, mon["left"], mon["top"]


def is_black(img: np.ndarray) -> bool:
    """True when the desktop is not being rendered (locked / minimised RDP session)."""
    if img.size == 0:
        return True
    return int(img[::4, ::4, :3].max()) < 3


PW_CLIENTONLY, PW_RENDERFULLCONTENT = 0x1, 0x2


def print_window(hwnd: int) -> np.ndarray | None:
    """Client area via PrintWindow. Slower (forces a repaint) but works for covered windows."""
    l, t, r, b = win32gui.GetClientRect(hwnd)
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        return None
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    mem_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        mem_dc.SelectObject(bmp)
        ok = ctypes.windll.user32.PrintWindow(wintypes.HWND(hwnd), wintypes.HDC(mem_dc.GetSafeHdc()),
                                              PW_CLIENTONLY | PW_RENDERFULLCONTENT)
        if not ok:
            return None
        raw = bmp.GetBitmapBits(True)
        return np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 4).copy()
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        mem_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
