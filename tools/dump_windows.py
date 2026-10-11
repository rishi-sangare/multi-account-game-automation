"""Phase-0 spike: list top-level windows (or one window's child tree) with class names.

    python tools/dump_windows.py                 # all visible top-level windows
    python tools/dump_windows.py --title ProTanki  # child tree of matching windows
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import win32gui  # noqa: E402

from commander.win32 import windows as W  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", help="regex for the window title")
    a = ap.parse_args()
    W.set_dpi_aware()
    sys.stdout.reconfigure(encoding="utf-8")
    if not a.title:
        def cb(h, _):
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h):
                r = W.client_rect_screen(h)
                print(f"0x{h:08X}  pid={W.window_pid(h):<6} class={W.class_name(h)!r:<38} "
                      f"client={r.w}x{r.h}  title={W.title(h)!r}")
            return True
        win32gui.EnumWindows(cb, None)
        return 0
    seen = set()
    while True:
        h = W.find_by_title(a.title, exclude=seen)
        if not h:
            break
        seen.add(h)
        print(f"\n=== {W.title(h)!r}  pid {W.window_pid(h)}  client {W.client_size(h)}")
        print("\n".join(W.describe_tree(h)))
    if not seen:
        print("no window matches", a.title)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
