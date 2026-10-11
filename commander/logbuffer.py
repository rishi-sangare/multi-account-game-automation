"""Logging setup: rotating file on disk + an in-memory ring the UI tails."""
from __future__ import annotations

import collections
import logging
import logging.handlers
import threading
import time
from dataclasses import dataclass

from . import paths


@dataclass
class LogLine:
    seq: int
    t: float
    level: int
    name: str
    text: str


class RingHandler(logging.Handler):
    def __init__(self, capacity: int = 2000):
        super().__init__()
        self.lines: collections.deque[LogLine] = collections.deque(maxlen=capacity)
        self.seq = 0
        self._lk = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        with self._lk:
            self.seq += 1
            self.lines.append(LogLine(self.seq, record.created, record.levelno, record.name, msg))

    def since(self, seq: int) -> list[LogLine]:
        with self._lk:
            return [ln for ln in self.lines if ln.seq > seq]


RING = RingHandler()


def setup(level: int = logging.INFO) -> None:
    paths.ensure_dirs()
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s.%(msecs)03d  %(levelname)-7s %(name)-8s %(message)s", "%H:%M:%S")
    fh = logging.handlers.TimedRotatingFileHandler(paths.LOGS_DIR / "commander.log", when="midnight",
                                                   backupCount=14, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(RING)
    logging.getLogger("commander").info("log started %s", time.strftime("%Y-%m-%d %H:%M:%S"))
