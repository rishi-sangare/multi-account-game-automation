"""Phase-0 spike: can we see the game's pixels? Screen grab vs PrintWindow, visible vs covered.

    python tools/spike_capture.py --title ProTanki

Saves PNGs to logs/spike/ and prints timing plus a black-frame verdict for each method.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commander import imaging, paths  # noqa: E402
from commander.win32 import capture  # noqa: E402
from commander.win32 import windows as W  # noqa: E402


def timed(fn, n=10):
    t0 = time.perf_counter()
    out = None
    for _ in range(n):
        out = fn()
    return out, (time.perf_counter() - t0) / n * 1000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    a = ap.parse_args()
    W.set_dpi_aware()
    hwnd = W.find_by_title(a.title)
    if not hwnd:
        print("window not found")
        return 1
    out = paths.LOGS_DIR / "spike"
    out.mkdir(parents=True, exist_ok=True)
    rect = W.client_rect_screen(hwnd)
    print(f"window 0x{hwnd:X} client {rect.w}x{rect.h} at ({rect.left},{rect.top})")
    img, ms = timed(lambda: capture.grab(rect))
    imaging.save_png(out / "screen_grab.png", img)
    print(f"screen grab (mss)      {ms:6.1f} ms   black={capture.is_black(img)}   -> {out / 'screen_grab.png'}")
    img2, ms2 = timed(lambda: capture.print_window(hwnd), 5)
    if img2 is None:
        print("PrintWindow            failed")
    else:
        imaging.save_png(out / "print_window.png", img2)
        print(f"PrintWindow(full)      {ms2:6.1f} ms   black={capture.is_black(img2)}   -> {out / 'print_window.png'}")
    print("\nNow cover the game with another window and run again: if PrintWindow still shows the game, "
          "covered windows can be watched with the PrintWindow fallback.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
