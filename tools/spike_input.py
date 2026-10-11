"""Phase-0 spike: does the game accept background clicks? Tries each delivery variant in turn.

Open the real client, put it BEHIND another window (unfocused), then:

    python tools/spike_input.py --title ProTanki --x 0.84 --y 0.90

For every variant the script clicks the given point (ratio of the client area) and asks you whether
the game reacted. The result tells you which `input_mode` to use for the account.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import win32api  # noqa: E402
import win32con  # noqa: E402
import win32gui  # noqa: E402

from commander.win32 import windows as W  # noqa: E402
from commander.win32.inputs import SendInputBackend  # noqa: E402


def variants():
    def post(target, pt, hold=0, activate=False, send=False, top=None):
        fn = win32gui.SendMessage if send else win32gui.PostMessage
        lp = win32api.MAKELONG(pt[0], pt[1])
        if activate:
            win32gui.PostMessage(top, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
            win32gui.PostMessage(target, win32con.WM_SETFOCUS, 0, 0)
        fn(target, win32con.WM_MOUSEMOVE, 0, lp)
        fn(target, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp)
        if hold:
            time.sleep(hold / 1000)
        fn(target, win32con.WM_LBUTTONUP, 0, lp)

    yield "PostMessage → deepest child", lambda top, x, y: post(*W.deepest_child(top, x, y))
    yield "SendMessage → deepest child", lambda top, x, y: post(*W.deepest_child(top, x, y), send=True)
    yield "PostMessage + WM_ACTIVATE/SETFOCUS", lambda top, x, y: post(*W.deepest_child(top, x, y), activate=True, top=top)
    yield "PostMessage with 30 ms hold", lambda top, x, y: post(*W.deepest_child(top, x, y), hold=30)
    yield "PostMessage → top-level window", lambda top, x, y: post(top, (x, y))
    yield "SendInput (focus + real mouse)", lambda top, x, y: SendInputBackend().click(top, x, y)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--x", type=float, required=True, help="ratio 0..1 of client width")
    ap.add_argument("--y", type=float, required=True, help="ratio 0..1 of client height")
    a = ap.parse_args()
    W.set_dpi_aware()
    top = W.find_by_title(a.title)
    if not top:
        print("window not found")
        return 1
    cw, ch = W.client_size(top)
    x, y = int(a.x * cw), int(a.y * ch)
    child, _ = W.deepest_child(top, x, y)
    print(f"window 0x{top:X} class={W.class_name(top)!r} client {cw}x{ch}; target child 0x{child:X} "
          f"class={W.class_name(child)!r}; point ({x},{y})\n")
    results = []
    for name, fn in variants():
        input(f"[{name}]  reset the game screen, then press Enter to click… ")
        fn(top, x, y)
        ok = input("  did the game react? [y/N] ").strip().lower() == "y"
        results.append((name, ok))
    print("\nresult:")
    for name, ok in results:
        print(f"  {'YES' if ok else ' no'}  {name}")
    best = next((n for n, ok in results if ok), None)
    mode = "sendinput" if best and best.startswith("SendInput") else ("post" if best else "?")
    print(f"\n→ recommended input_mode: {mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
