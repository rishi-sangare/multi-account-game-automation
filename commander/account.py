"""Per-account runtime: the state machine plus a small worker that executes click sequences."""
from __future__ import annotations

import enum
import logging
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field

from .config import Account, Preset, Settings, Step
from .triggers import CompiledPreset, Debouncer
from .win32 import windows as W
from .win32.inputs import BACKENDS

log = logging.getLogger("engine")


class S(str, enum.Enum):
    STOPPED = "STOPPED"
    LAUNCHING = "LAUNCHING"
    CONNECTING = "CONNECTING"
    WATCHING = "WATCHING"
    JOINING = "JOINING"
    IN_BATTLE = "IN_BATTLE"
    EXITING = "EXITING"
    CLOSING = "CLOSING"
    EXITED = "EXITED"
    PAUSED = "PAUSED"
    ERROR = "ERROR"


LIVE_STATES = {S.CONNECTING, S.WATCHING, S.JOINING, S.IN_BATTLE, S.EXITING, S.EXITED, S.PAUSED, S.CLOSING}


@dataclass
class Stats:
    clicks: int = 0
    joins: int = 0
    misses: int = 0
    retries: int = 0
    react_last: float | None = None      # ms, upper bound: last "not ready" frame -> click sent
    react_sum: float = 0.0
    react_n: int = 0
    confirm_last: float | None = None    # ms, click -> in-battle confirmed on screen
    exits: int = 0
    relaunches: int = 0

    @property
    def react_avg(self) -> float | None:
        return self.react_sum / self.react_n if self.react_n else None


# ------------------------------------------------------------------ action worker


class ActionWorker(threading.Thread):
    """Executes sequences for one account. Waits are interruptible so EXIT ALL pre-empts instantly."""

    def __init__(self, acc: "AccountRuntime"):
        super().__init__(name=f"act-{acc.cfg.id}", daemon=True)
        self.acc = acc
        self.q: queue.Queue = queue.Queue()
        self.abort = threading.Event()
        self.alive = True

    def submit(self, name: str, steps: list[Step], priority: bool = False) -> None:
        if priority:
            self.abort.set()
            try:
                while True:
                    self.q.get_nowait()
            except queue.Empty:
                pass
        self.q.put((name, steps))

    def stop(self) -> None:
        self.alive = False
        self.abort.set()
        self.q.put(None)

    def run(self) -> None:
        while self.alive:
            item = self.q.get()
            if item is None:
                break
            self.abort.clear()
            name, steps = item
            try:
                self._run_steps(name, steps)
            except Exception as e:                                  # window vanished mid-sequence etc.
                log.warning("%s · sequence '%s' failed: %s", self.acc.cfg.id, name, e)

    def _run_steps(self, name: str, steps: list[Step]) -> None:
        acc = self.acc
        for st in steps:
            if self.abort.is_set():
                return
            hwnd, cp = acc.hwnd, acc.compiled
            if not W.is_window(hwnd) or cp is None:
                return
            backend = BACKENDS[acc.effective_input]
            if st.click is not None:
                x, y = cp.point_px(st.click)
                backend.click(hwnd, x, y, st.hold_ms)
                acc.stats.clicks += 1
                acc.last_click = (st.click, x, y, backend.name)
            elif st.key is not None:
                backend.key(hwnd, st.key, st.hold_ms)
            elif st.wait_ms is not None:
                if self.abort.wait(st.wait_ms / 1000):
                    return


# ------------------------------------------------------------------ runtime


@dataclass
class AccountRuntime:
    cfg: Account
    slot: int
    preset: Preset | None = None
    preset_error: str | None = None
    compiled: CompiledPreset | None = None
    debouncer: Debouncer | None = None
    hwnd: int = 0
    pid: int = 0
    pids: frozenset = frozenset()          # process tree cached at bind time (for RAM metrics)
    proc: subprocess.Popen | None = None
    state: S = S.STOPPED
    since: float = field(default_factory=time.perf_counter)
    stats: Stats = field(default_factory=Stats)
    obs: dict[str, bool] = field(default_factory=dict)
    last_event: str = "idle"
    last_event_t: float = field(default_factory=time.time)
    last_click: tuple | None = None
    error: str | None = None
    escalated: bool = False
    # timers / counters used by the state machine
    t_last_not_ready: float = 0.0
    t_first_click: float = 0.0
    t_click: float = 0.0
    attempts: int = 0
    t_exit_sent: float = 0.0
    exit_attempts: int = 0
    t_last_lobby: float = 0.0
    t_afk: float = 0.0
    launch_attempts: int = 0
    t_launch: float = 0.0
    t_next_bind: float = 0.0
    t_covered_log: float = -999.0
    resume_to: S = S.CONNECTING
    exit_mode: str = "leave_battle"        # mode of the EXIT ALL currently in progress
    worker: ActionWorker | None = None

    def __post_init__(self) -> None:
        self.worker = ActionWorker(self)
        self.worker.start()

    # ---------------------------------------------------------- helpers

    @property
    def id(self) -> str:
        return self.cfg.id

    @property
    def effective_input(self) -> str:
        return "sendinput" if self.escalated else self.cfg.input_mode

    def event(self, text: str, level: int = logging.INFO, display: str | None = None) -> None:
        self.last_event = display or text
        self.last_event_t = time.time()
        log.log(level, "%s · %s", self.cfg.id, text)

    def set_state(self, new: S, reason: str = "", now: float | None = None, quiet: bool = False) -> None:
        if new == self.state:
            return
        old, self.state = self.state, new
        self.since = now if now is not None else time.perf_counter()
        if not quiet:
            display = reason[:1].upper() + reason[1:] if reason else new.value.replace("_", " ").capitalize()
            self.event(f"{old.value} → {new.value}" + (f"  ({reason})" if reason else ""),
                       logging.WARNING if new == S.ERROR else logging.INFO, display)

    def run_seq(self, name: str, priority: bool = False) -> bool:
        if not self.preset or name not in self.preset.sequences:
            return False
        self.worker.submit(name, self.preset.sequences[name], priority)
        return True

    def window_ok(self) -> bool:
        return W.is_window(self.hwnd)

    def ensure_compiled(self) -> CompiledPreset | None:
        if not self.preset or not self.window_ok():
            return None
        cw, ch = W.client_size(self.hwnd)
        if cw <= 0 or ch <= 0:
            return None
        if self.compiled is None or (self.compiled.cw, self.compiled.ch) != (cw, ch) \
                or self.compiled.preset is not self.preset:
            from .triggers import compile_preset
            self.compiled = compile_preset(self.preset, cw, ch)
            self.debouncer = Debouncer(self.preset)
        return self.compiled

    def needs_capture(self) -> bool:
        return self.state in LIVE_STATES and self.state != S.CLOSING and self.window_ok()

    # ---------------------------------------------------------- state machine

    def step(self, obs: dict[str, bool], now: float, settings: Settings) -> None:
        self.obs = obs
        st = self.state
        if st not in LIVE_STATES or st in (S.PAUSED, S.CLOSING) or not self.preset:
            return
        to = self.preset.timeouts
        J = obs.get("join_ready", False)
        B = obs.get("in_battle", False)
        L = obs.get("lobby", False)
        if L:
            self.t_last_lobby = now

        if st == S.CONNECTING:
            if B:
                self.set_state(S.IN_BATTLE, "already in battle", now)
            elif L or J:
                if self.run_seq("open_list"):
                    self.event("opening battle list")
                self.t_last_not_ready = now
                self.set_state(S.WATCHING, "lobby detected", now)
            elif now - self.since > to.connect_s and now - self.t_afk > to.connect_s:
                self.t_afk = now
                self.event("still waiting for the lobby screen - logged in?", logging.WARNING)

        elif st == S.WATCHING:
            if B:
                self.set_state(S.IN_BATTLE, "joined manually", now)
            elif J:
                react = (now - self.t_last_not_ready) * 1000.0
                self.stats.react_last = react
                self.attempts = 0
                self.t_first_click = self.t_click = now
                self.run_seq("join")
                self.set_state(S.JOINING, f"join_ready ✓ → click · ≤{react:.0f} ms", now)
            else:
                self.t_last_not_ready = now
                if not L and now - self.t_last_lobby > to.stuck_s and now - self.since > to.stuck_s:
                    self.t_last_lobby = now
                    if self.run_seq("recover"):
                        self.event("no lobby for a while → recover sequence", logging.WARNING)

        elif st == S.JOINING:
            if B:
                self.stats.joins += 1
                self.stats.confirm_last = (now - self.t_first_click) * 1000.0
                if self.stats.react_last is not None:
                    self.stats.react_sum += self.stats.react_last
                    self.stats.react_n += 1
                self.t_afk = now
                self.set_state(S.IN_BATTLE, f"confirmed in {self.stats.confirm_last:.0f} ms", now)
            elif now - self.t_click >= settings.verify_ms / 1000.0:
                if J:                                             # button still there: click didn't land
                    if self.attempts < settings.max_retries:
                        self.attempts += 1
                        self.stats.retries += 1
                        self.t_click = now
                        self.run_seq("join")
                        self.event(f"verify failed → retry {self.attempts}/{settings.max_retries}",
                                   logging.WARNING)
                    elif not self.escalated and self.cfg.input_mode == "post":
                        self.escalated = True
                        self.attempts = 0
                        self.t_click = now
                        self.run_seq("join")
                        self.event("posted clicks ignored → switching to SendInput", logging.WARNING)
                    else:
                        self.stats.misses += 1
                        self.set_state(S.WATCHING, "join failed after retries", now)
                elif L and now - self.t_click > 2 * settings.verify_ms / 1000.0:
                    self.stats.misses += 1
                    self.t_last_not_ready = now
                    self.set_state(S.WATCHING, "slot closed before join", now)
                elif now - self.t_first_click > to.join_s:
                    self.stats.misses += 1
                    self.t_last_not_ready = now
                    self.set_state(S.WATCHING, "join not confirmed (timeout)", now)

        elif st == S.IN_BATTLE:
            if L and not B:
                self.t_last_not_ready = now
                self.set_state(S.WATCHING, "battle finished", now)
            elif settings.anti_afk and now - self.t_afk > to.anti_afk_every_s:
                self.t_afk = now
                if self.run_seq("anti_afk"):
                    self.event("anti-AFK nudge")

        elif st == S.EXITING:
            if not B and L:
                self.stats.exits += 1
                if self.exit_mode == "close_client":
                    self.begin_close(now)
                else:
                    self.set_state(S.EXITED, "back in lobby", now)
            elif now - self.t_exit_sent > to.exit_retry_s:
                if self.exit_attempts < 3:
                    self.exit_attempts += 1
                    self.t_exit_sent = now
                    self.run_seq("exit", priority=True)
                    self.event(f"still in battle → exit again ({self.exit_attempts}/3)", logging.WARNING)
                else:
                    self.event("exit sequence not working → closing client", logging.WARNING)
                    self.begin_close(now)

        elif st == S.EXITED:
            if B and now - self.since < 6.0:                      # a join click that landed late
                self.begin_exit(now, "late join caught")

    # ---------------------------------------------------------- exit helpers

    def begin_exit(self, now: float, reason: str = "EXIT ALL", mode: str | None = None) -> None:
        if mode:
            self.exit_mode = mode
        self.exit_attempts = 0
        self.t_exit_sent = now
        self.run_seq("exit", priority=True)
        self.set_state(S.EXITING, reason, now)

    def begin_close(self, now: float) -> None:
        if self.window_ok():
            BACKENDS["post"].close(self.hwnd)
        self.set_state(S.CLOSING, "WM_CLOSE sent", now)
