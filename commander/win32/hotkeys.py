"""System-wide hotkeys via RegisterHotKey (no keyboard hook)."""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable

from .inputs import vk_of

log = logging.getLogger("hotkeys")
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}


def parse(combo: str) -> tuple[int, int]:
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    mods = 0
    for p in parts[:-1]:
        if p not in _MODS:
            raise ValueError(f"unknown modifier '{p}'")
        mods |= _MODS[p]
    return mods | MOD_NOREPEAT, vk_of(parts[-1])


def pretty(combo: str) -> str:
    return "+".join(p.strip().capitalize() if len(p.strip()) > 1 else p.strip().upper()
                    for p in combo.split("+"))


class HotkeyThread(threading.Thread):
    """Owns the registrations and a tiny message loop (0 % CPU while idle)."""

    def __init__(self, bindings: dict[str, str], on_hotkey: Callable[[str], None]):
        super().__init__(name="hotkeys", daemon=True)
        self.bindings = bindings
        self.on_hotkey = on_hotkey
        self.registered: dict[str, bool] = {}
        self._tid = 0
        self._ready = threading.Event()

    def run(self) -> None:
        self._tid = kernel32.GetCurrentThreadId()
        ids: dict[int, str] = {}
        for i, (action, combo) in enumerate(self.bindings.items(), start=1):
            try:
                mods, vk = parse(combo)
                ok = bool(user32.RegisterHotKey(None, i, mods, vk))
            except ValueError as e:
                log.error("hotkey %s=%s invalid: %s", action, combo, e)
                ok = False
            self.registered[action] = ok
            if ok:
                ids[i] = action
                log.info("hotkey armed  %-10s %s", action, pretty(combo))
            else:
                log.warning("hotkey %s (%s) could not be registered - already used by another app?",
                            action, pretty(combo))
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in ids:
                try:
                    self.on_hotkey(ids[msg.wParam])
                except Exception:                                   # never kill the hotkey loop
                    log.exception("hotkey handler failed")
        for i in ids:
            user32.UnregisterHotKey(None, i)

    def wait_ready(self, timeout: float = 2.0) -> None:
        self._ready.wait(timeout)

    def stop(self) -> None:
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
