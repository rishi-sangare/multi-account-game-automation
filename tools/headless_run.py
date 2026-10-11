"""Run the engine without the UI (smoke test / CLI mode).

    python tools/headless_run.py --accounts acc1,acc2,acc3 --seconds 40 --exit-at 30
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commander import config as C  # noqa: E402
from commander import logbuffer  # noqa: E402
from commander.engine import Engine  # noqa: E402
from commander.win32.windows import set_dpi_aware  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", default="")
    ap.add_argument("--seconds", type=float, default=40)
    ap.add_argument("--exit-at", type=float, default=None)
    ap.add_argument("--mode", default=None, help="exit mode override")
    a = ap.parse_args()

    set_dpi_aware()
    sys.stdout.reconfigure(encoding="utf-8")
    logbuffer.setup()
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-7s %(message)s", "%H:%M:%S"))
    logging.getLogger().addHandler(console)

    accounts = C.load_accounts()
    if a.accounts:
        want = set(a.accounts.split(","))
        accounts = [x for x in accounts if x.id in want]
    eng = Engine(C.load_settings(), accounts)
    eng.start()
    eng.submit("START")
    t0 = time.time()
    exited = False
    try:
        while time.time() - t0 < a.seconds:
            time.sleep(1)
            if a.exit_at and not exited and time.time() - t0 >= a.exit_at:
                eng.submit("EXIT_ALL", arg=a.mode)
                exited = True
    finally:
        snap = eng.snapshot()
        print("\n--- summary ---")
        print(f"tick {snap.tick_ms:.1f} ms  work {snap.work_ms:.2f} ms  cpu {snap.cpu:.1f}%  ram {snap.ram_mb:.0f} MB  "
              f"clients {snap.clients_ram_mb:.0f} MB  last exit {snap.last_exit_ms}")
        for s in snap.accounts:
            print(f"{s.id:6} {s.state:11} joins={s.joins} miss={s.misses} retries={s.retries} clicks={s.clicks} "
                  f"react_avg={s.react_avg and round(s.react_avg)} confirm={s.confirm_last and round(s.confirm_last)}")
        eng.submit("KILL_ALL")
        time.sleep(1.5)
        eng.shutdown()
        eng.join(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
