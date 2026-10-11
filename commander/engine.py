"""The engine: one thread that captures, decides and dispatches for every account.

Each tick (poll_ms):
    1. drain commands (UI / hotkey / Telegram)
    2. capture a minimal rectangle per live account
    3. evaluate triggers -> debounced facts
    4. advance each account's state machine (actions go to its worker thread)
    5. watchdog (every 0.5 s): crashed clients, lost windows, minimised windows, black desktop
    6. publish an immutable snapshot for the UI
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass

import psutil

from . import config as C
from . import paths
from .account import LIVE_STATES, AccountRuntime, S
from .triggers import View
from .win32 import capture
from .win32 import windows as W
from .win32.inputs import BACKENDS
from .win32.windows import Rect

log = logging.getLogger("engine")

CREATE_NEW_PROCESS_GROUP = 0x00000200


# ------------------------------------------------------------------ snapshots (read by the UI)


@dataclass(frozen=True)
class AccountSnap:
    id: str
    name: str
    slot: int
    enabled: bool
    preset: str
    input_mode: str
    escalated: bool
    state: str
    state_age: float
    hwnd: int
    pid: int
    client: tuple[int, int]
    obs: dict
    clicks: int
    joins: int
    misses: int
    retries: int
    react_last: float | None
    react_avg: float | None
    confirm_last: float | None
    last_event: str
    last_event_t: float
    error: str | None


@dataclass(frozen=True)
class EngineSnap:
    t: float = 0.0
    accounts: tuple[AccountSnap, ...] = ()
    tick_ms: float = 0.0
    work_ms: float = 0.0
    capture_ms: float = 0.0
    rendering_ok: bool = True
    exit_mode: str = "leave_battle"
    exit_in_progress: bool = False
    last_exit_ms: float | None = None
    last_exit_count: int = 0
    cpu: float = 0.0
    ram_mb: float = 0.0
    clients_ram_mb: float = 0.0
    paused_all: bool = False


@dataclass
class Cmd:
    kind: str
    target: str | None = None
    arg: object = None


# ------------------------------------------------------------------ engine


class Engine(threading.Thread):
    def __init__(self, settings: C.Settings, accounts: list[C.Account]):
        super().__init__(name="engine", daemon=True)
        self.settings = settings
        self.cmds: queue.Queue[Cmd] = queue.Queue()
        self.stop_evt = threading.Event()
        self.accounts: dict[str, AccountRuntime] = {}
        self.presets: dict[str, C.Preset] = {}
        self._snap = EngineSnap(exit_mode=settings.exit_mode)
        self._tick_ms = 0.0
        self._work_ms = 0.0
        self._cap_ms = 0.0
        self._black_since: float | None = None
        self._rendering_ok = True
        self._exit_t0: float | None = None
        self._exit_ids: set[str] = set()
        self._last_exit_ms: float | None = None
        self._last_exit_n = 0
        self._next_watchdog = 0.0
        self._next_metrics = 0.0
        self._cfg_mtime = C.presets_mtime()
        self._next_cfg_check = 0.0
        self._me = psutil.Process()
        self._procs: dict[int, psutil.Process] = {}
        self._me.cpu_percent(None)
        self._cpu = 0.0
        self._ram = 0.0
        self._clients_ram = 0.0
        self._sync_accounts(accounts)

    # ---------------------------------------------------------- public API (any thread)

    def submit(self, kind: str, target: str | None = None, arg: object = None) -> None:
        self.cmds.put(Cmd(kind, target, arg))

    def snapshot(self) -> EngineSnap:
        return self._snap

    def shutdown(self) -> None:
        self.stop_evt.set()

    # ---------------------------------------------------------- loop

    def run(self) -> None:
        log.info("engine started · poll %d ms · verify %d ms · exit mode %s",
                 self.settings.poll_ms, self.settings.verify_ms, self.settings.exit_mode)
        while not self.stop_evt.is_set():
            t0 = time.perf_counter()
            try:
                self._drain()
                self._tick(t0)
                if t0 >= self._next_watchdog:
                    self._next_watchdog = t0 + 0.5
                    self._watchdog(t0)
                if t0 >= self._next_metrics:
                    self._next_metrics = t0 + 1.0
                    self._metrics()
                if t0 >= self._next_cfg_check:
                    self._next_cfg_check = t0 + 1.0
                    self._check_config_files()
            except Exception:
                log.exception("engine tick failed (continuing)")
            work = (time.perf_counter() - t0) * 1000
            self._work_ms = self._work_ms * 0.9 + work * 0.1
            self._publish()
            self.stop_evt.wait(max(0.0, self.settings.poll_ms / 1000 - (time.perf_counter() - t0)))
            self._tick_ms = (time.perf_counter() - t0) * 1000
        for acc in self.accounts.values():
            acc.worker.stop()
        log.info("engine stopped")

    # ---------------------------------------------------------- tick

    def _tick(self, now: float) -> None:
        # 1. which accounts need pixels, and where (screen coords)
        jobs: list[tuple[AccountRuntime, int, int, Rect]] = []
        for acc in self.accounts.values():
            if acc.state == S.LAUNCHING:
                if now >= acc.t_next_bind:                     # EnumWindows is not free: 4x per second
                    acc.t_next_bind = now + 0.25
                    self._try_bind(acc, now)
                continue
            if not acc.needs_capture():
                continue
            cp = acc.ensure_compiled()
            if cp is None:
                continue
            l, t, r, b = cp.box
            try:
                sx, sy = W.client_to_screen(acc.hwnd, l, t)
            except Exception:
                continue                                       # window closed between checks
            jobs.append((acc, l, t, Rect(sx, sy, sx + (r - l), sy + (b - t))))
        if not jobs:
            self._track_rendering(False, True, now)
            self._track_exit_all(time.perf_counter())
            return

        # 2. ONE desktop grab covering every box. Each GDI grab costs ~6 ms of compositor sync no
        #    matter how small, so one union grab beats N small ones (12 ms vs 6 ms x N).
        L = min(j[3].left for j in jobs)
        T = min(j[3].top for j in jobs)
        R = max(j[3].right for j in jobs)
        B = max(j[3].bottom for j in jobs)
        t_cap = time.perf_counter()
        try:
            frame = capture.grab(Rect(L, T, R, B))
        except Exception:
            log.exception("screen capture failed")
            return
        self._cap_ms = self._cap_ms * 0.9 + (time.perf_counter() - t_cap) * 1000 * 0.1
        now = time.perf_counter()                              # decisions are timed from the fresh frame

        # 3. evaluate + advance each account from its slice of the frame
        all_black = True
        for acc, l, t, box in jobs:
            img = frame[box.top - T:box.bottom - T, box.left - L:box.right - L]
            if not capture.is_black(img):
                all_black = False
            raw = acc.compiled.evaluate(View(img, l, t))
            obs = acc.debouncer.feed(raw)
            try:
                acc.step(obs, now, self.settings)
            except Exception:
                log.exception("%s · state machine error", acc.id)
        self._track_rendering(True, all_black, now)
        self._track_exit_all(time.perf_counter())

    def _track_rendering(self, any_capture: bool, all_black: bool, now: float) -> None:
        if not any_capture:
            self._black_since = None
            return
        if all_black:
            self._black_since = self._black_since or now
            if self._rendering_ok and now - self._black_since > 2.0:
                self._rendering_ok = False
                log.error("desktop is not rendering (RDP minimised or disconnected?) - "
                          "use vps/fix_rdp_minimized.reg and Disconnect-Keep-Running.bat")
        else:
            self._black_since = None
            if not self._rendering_ok:
                log.info("desktop rendering again")
            self._rendering_ok = True

    def _track_exit_all(self, now: float) -> None:
        if self._exit_t0 is None:
            return
        done_states = {S.EXITED, S.STOPPED, S.ERROR, S.PAUSED}
        pending = [i for i in self._exit_ids if i in self.accounts and self.accounts[i].state not in done_states]
        if not pending:
            ms = (now - self._exit_t0) * 1000
            self._last_exit_ms, self._last_exit_n = ms, len(self._exit_ids)
            log.info("EXIT ALL complete · %d account(s) out in %.0f ms", len(self._exit_ids), ms)
            self._exit_t0 = None
        elif now - self._exit_t0 > 15:
            log.warning("EXIT ALL still pending for: %s", ", ".join(sorted(pending)))
            self._exit_t0 = None

    # ---------------------------------------------------------- commands

    def _drain(self) -> None:
        while True:
            try:
                cmd = self.cmds.get_nowait()
            except queue.Empty:
                return
            try:
                self._handle(cmd)
            except Exception:
                log.exception("command %s failed", cmd.kind)

    def _targets(self, target: str | None) -> list[AccountRuntime]:
        if target is None:
            return [a for a in self.accounts.values() if a.cfg.enabled]
        a = self.accounts.get(target)
        return [a] if a else []

    def _handle(self, cmd: Cmd) -> None:
        now = time.perf_counter()
        k = cmd.kind
        if k == "START":
            for acc in self._targets(cmd.target):
                self._start(acc, now)
        elif k == "PAUSE":
            for acc in self._targets(cmd.target):
                if acc.state in LIVE_STATES and acc.state not in (S.PAUSED, S.CLOSING):
                    acc.worker.abort.set()
                    acc.set_state(S.PAUSED, "paused", now)
        elif k == "STOP":                                          # forget the window, keep it open
            for acc in self._targets(cmd.target):
                acc.worker.abort.set()
                acc.hwnd, acc.pid, acc.proc = 0, 0, None
                acc.set_state(S.STOPPED, "stopped", now)
        elif k == "EXIT_ALL":
            self._exit_all(now, cmd.arg or self.settings.exit_mode)
        elif k == "KILL_ALL":
            self._exit_all(now, "kill")
        elif k == "PAUSE_ALL":
            self._handle(Cmd("PAUSE"))
        elif k == "TILE":
            for acc in self._targets(cmd.target):
                self._tile(acc)
        elif k == "CLICK":                                         # test a point from the UI
            acc = self.accounts.get(cmd.target)
            if acc and acc.ensure_compiled() and cmd.arg in acc.preset.points:
                x, y = acc.compiled.point_px(cmd.arg)
                BACKENDS[acc.effective_input].click(acc.hwnd, x, y)
                acc.event(f"test click '{cmd.arg}' at ({x},{y}) via {acc.effective_input}")
        elif k == "RUN_SEQ":
            acc = self.accounts.get(cmd.target)
            if acc and acc.ensure_compiled():
                if acc.run_seq(cmd.arg):
                    acc.event(f"test sequence '{cmd.arg}'")
        elif k == "BIND":                                          # attach to a picked window
            acc = self.accounts.get(cmd.target)
            if acc and W.is_window(cmd.arg):
                self._bind(acc, int(cmd.arg), now)
        elif k == "SET_SETTINGS":
            self.settings = cmd.arg
            log.info("settings applied · poll %d ms · verify %d ms · exit mode %s",
                     self.settings.poll_ms, self.settings.verify_ms, self.settings.exit_mode)
        elif k == "RELOAD":
            self._reload_all()
        elif k == "RESET_STATS":
            for acc in self._targets(cmd.target):
                acc.stats = type(acc.stats)()
            self._last_exit_ms = None
        elif k == "REMOVE":
            acc = self.accounts.pop(cmd.target, None)
            if acc:
                acc.worker.stop()

    # ---------------------------------------------------------- lifecycle

    def _start(self, acc: AccountRuntime, now: float) -> None:
        if acc.preset is None:
            acc.error = acc.preset_error
            acc.set_state(S.ERROR, acc.preset_error or "no preset", now)
            return
        acc.escalated = False
        acc.error = None
        if acc.state in (S.PAUSED, S.EXITED, S.ERROR) and acc.window_ok():
            acc.t_last_not_ready = now
            acc.set_state(S.CONNECTING, "resumed", now)
            return
        if acc.state in LIVE_STATES:
            return
        if acc.window_ok():
            acc.set_state(S.CONNECTING, "window already bound", now)
            return
        if acc.cfg.mode == "attach":
            hwnd = self._find_attach_window(acc)
            if hwnd:
                self._bind(acc, hwnd, now)
            else:
                acc.error = "window not found - use 'Bind window' and click the game"
                acc.set_state(S.ERROR, acc.error, now)
            return
        acc.launch_attempts = 0
        self._launch(acc, now)

    def _launch(self, acc: AccountRuntime, now: float) -> None:
        cfg = acc.cfg
        if cfg.exe == "{mock}":
            argv = paths.mock_command() + list(cfg.args)
            if "--name" not in cfg.args:
                argv += ["--name", cfg.name]
        else:
            if not cfg.exe or not os.path.exists(cfg.exe):
                acc.error = f"executable not found: {cfg.exe or '(empty)'}"
                acc.set_state(S.ERROR, acc.error, now)
                return
            argv = [cfg.exe] + list(cfg.args)
        cwd = cfg.workdir or (os.path.dirname(cfg.exe) if cfg.exe not in ("", "{mock}") else str(paths.ROOT))
        try:
            acc.proc = subprocess.Popen(argv, cwd=cwd, creationflags=CREATE_NEW_PROCESS_GROUP,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
        except OSError as e:
            acc.error = f"launch failed: {e}"
            acc.set_state(S.ERROR, acc.error, now)
            return
        acc.pid = acc.proc.pid
        acc.hwnd = 0
        acc.launch_attempts += 1
        acc.t_launch = now
        acc.set_state(S.LAUNCHING, f"pid {acc.pid}", now)

    def _try_bind(self, acc: AccountRuntime, now: float) -> None:
        if now - acc.t_launch > (acc.preset.timeouts.launch_s if acc.preset else 60):
            self._kill(acc)
            if acc.launch_attempts < 3:
                acc.event(f"no window after launch → retry {acc.launch_attempts + 1}/3", logging.WARNING)
                self._launch(acc, now)
            else:
                acc.error = "client window never appeared"
                acc.set_state(S.ERROR, acc.error, now)
            return
        taken = {a.hwnd for a in self.accounts.values() if a is not acc and a.hwnd}
        wins = [h for h in W.top_windows_for_pids(W.process_tree(acc.pid)) if h not in taken]
        if wins:
            self._bind(acc, wins[0], now)

    def _bind(self, acc: AccountRuntime, hwnd: int, now: float) -> None:
        acc.hwnd = hwnd
        acc.pid = acc.pid or W.window_pid(hwnd)
        acc.pids = frozenset(W.process_tree(acc.pid) | {W.window_pid(hwnd)})
        acc.compiled = None
        if self.settings.rename_windows:
            W.set_title(hwnd, f"{acc.cfg.name} · ProTanki")
        if self.settings.layout.auto_tile:
            self._tile(acc)
        acc.t_last_not_ready = acc.t_last_lobby = now
        acc.set_state(S.CONNECTING, f"bound to window 0x{hwnd:X}", now)

    def _find_attach_window(self, acc: AccountRuntime) -> int | None:
        if not acc.cfg.window_title:
            return None
        taken = {a.hwnd for a in self.accounts.values() if a.hwnd}
        return W.find_by_title(acc.cfg.window_title, exclude=taken)

    def _tile(self, acc: AccountRuntime) -> None:
        if not acc.window_ok():
            return
        lay = self.settings.layout
        cap = W.caption_height(acc.hwnd)
        col, row = acc.slot % lay.cols, acc.slot // lay.cols
        cell_w = lay.client_w + 2 + lay.gap
        cell_h = lay.client_h + cap + 1 + lay.gap
        cx = lay.origin[0] + 1 + col * cell_w
        cy = lay.origin[1] + cap + row * cell_h
        W.place_client(acc.hwnd, cx, cy, lay.client_w, lay.client_h)
        acc.compiled = None

    def _kill(self, acc: AccountRuntime, wait: bool = True) -> list[psutil.Process]:
        pids = (W.process_tree(acc.pid) if acc.pid else set()) | set(acc.pids)
        if not pids and acc.window_ok():
            pids = {W.window_pid(acc.hwnd)}
        procs = []
        for p in pids:
            try:
                pr = psutil.Process(p)
                if pr.pid != os.getpid():
                    pr.kill()                                     # TerminateProcess
                    procs.append(pr)
            except psutil.Error:
                pass
        if wait:
            psutil.wait_procs(procs, timeout=1.0)
        acc.hwnd, acc.pid, acc.proc, acc.pids = 0, 0, None, frozenset()
        return procs

    def _exit_all(self, now: float, mode: str) -> None:
        participants = [a for a in self.accounts.values() if a.state in LIVE_STATES or a.state == S.LAUNCHING]
        log.warning("EXIT ALL (%s) → %d account(s)", mode, len(participants))
        self._exit_t0, self._exit_ids = now, {a.id for a in participants}
        if mode == "kill":
            procs: list[psutil.Process] = []
            for acc in participants:                              # terminate everything first …
                acc.worker.abort.set()
                procs += self._kill(acc, wait=False)
                acc.stats.exits += 1
                acc.set_state(S.STOPPED, "killed", now)
            psutil.wait_procs(procs, timeout=1.5)                 # … then wait once
            return                                                # completion logged by _track_exit_all
        for acc in participants:                                  # all posted in the same loop
            acc.worker.abort.set()
            if acc.state == S.LAUNCHING:
                self._kill(acc)
                acc.set_state(S.STOPPED, "launch cancelled", now)
            elif acc.state == S.IN_BATTLE or acc.obs.get("in_battle"):
                acc.begin_exit(now, mode=mode)
            elif mode == "close_client":
                acc.begin_close(now)
            else:
                acc.set_state(S.EXITED, "not in battle", now)

    # ---------------------------------------------------------- watchdog

    def _watchdog(self, now: float) -> None:
        for acc in self.accounts.values():
            st = acc.state
            if st == S.CLOSING:
                alive = acc.pid and psutil.pid_exists(acc.pid) and acc.window_ok()
                if not alive:
                    acc.hwnd, acc.pid, acc.proc = 0, 0, None
                    acc.set_state(S.STOPPED, "client closed", now)
                elif now - acc.since > 2.0:
                    self._kill(acc)
                    acc.set_state(S.STOPPED, "client killed after WM_CLOSE timeout", now)
                continue
            if st not in LIVE_STATES:
                continue
            proc_dead = acc.pid and not psutil.pid_exists(acc.pid) and not acc.window_ok()
            if not acc.window_ok() or proc_dead:
                acc.hwnd = 0
                if acc.cfg.mode == "launch" and self.settings.auto_relaunch and st not in (S.EXITED, S.PAUSED):
                    acc.stats.relaunches += 1
                    acc.event("client window lost (crashed or closed) → relaunching", logging.WARNING)
                    acc.launch_attempts = 0
                    self._launch(acc, now)
                else:
                    acc.pid, acc.proc = 0, None
                    acc.set_state(S.STOPPED, "client window closed", now)
                continue
            if W.is_minimized(acc.hwnd):
                W.restore_no_activate(acc.hwnd)
                acc.event("window was minimised → restored (minimised windows stop rendering)",
                          logging.WARNING)
            elif self.settings.raise_covered and st != S.PAUSED and W.is_covered(acc.hwnd):
                W.raise_no_activate(acc.hwnd)
                if now - acc.t_covered_log > 30:
                    acc.t_covered_log = now
                    acc.event("window was covered by another app → brought back on top", logging.WARNING)

    # ---------------------------------------------------------- config

    def _load_preset(self, name: str) -> tuple[C.Preset | None, str | None]:
        try:
            return C.load_preset(name), None
        except C.ConfigError as e:
            return None, str(e)

    def _sync_accounts(self, accounts: list[C.Account]) -> None:
        seen = set()
        for i, cfg in enumerate(accounts):
            seen.add(cfg.id)
            slot = cfg.slot if cfg.slot is not None else i
            preset, err = self._load_preset(cfg.preset)
            if cfg.id in self.accounts:
                acc = self.accounts[cfg.id]
                acc.cfg, acc.slot = cfg, slot
            else:
                acc = AccountRuntime(cfg=cfg, slot=slot)
                self.accounts[cfg.id] = acc
            acc.preset, acc.preset_error, acc.compiled = preset, err, None
            if err:
                log.error("%s · %s", cfg.id, err)
        for gone in [i for i in self.accounts if i not in seen]:
            self.accounts.pop(gone).worker.stop()
        # keep config order
        self.accounts = {a.id: self.accounts[a.id] for a in accounts if a.id in self.accounts}

    def _reload_all(self) -> None:
        try:
            self.settings = C.load_settings()
            self._sync_accounts(C.load_accounts())
            log.info("configuration reloaded")
        except C.ConfigError as e:
            log.error("reload failed: %s", e)

    def _check_config_files(self) -> None:
        m = C.presets_mtime()
        if m != self._cfg_mtime:
            self._cfg_mtime = m
            self._reload_all()

    # ---------------------------------------------------------- metrics / snapshot

    def _metrics(self) -> None:
        # NB: never walk process trees here - psutil's ppid map costs ~11 ms per call on Windows.
        try:
            self._cpu = self._me.cpu_percent(None) / max(psutil.cpu_count() or 1, 1)
            self._ram = self._me.memory_info().rss / 1048576
        except psutil.Error:
            pass
        total = 0
        for acc in self.accounts.values():
            if not acc.hwnd:
                continue
            for p in acc.pids:
                proc = self._procs.get(p)
                try:
                    if proc is None:
                        proc = self._procs[p] = psutil.Process(p)
                    total += proc.memory_info().rss
                except psutil.Error:
                    self._procs.pop(p, None)
        self._clients_ram = total / 1048576

    def _publish(self) -> None:
        now = time.perf_counter()
        snaps = []
        for acc in self.accounts.values():
            client = W.client_size(acc.hwnd) if acc.window_ok() else (0, 0)
            s = acc.stats
            snaps.append(AccountSnap(
                id=acc.id, name=acc.cfg.name, slot=acc.slot, enabled=acc.cfg.enabled, preset=acc.cfg.preset,
                input_mode=acc.effective_input, escalated=acc.escalated, state=acc.state.value,
                state_age=now - acc.since, hwnd=acc.hwnd, pid=acc.pid, client=client, obs=dict(acc.obs),
                clicks=s.clicks, joins=s.joins, misses=s.misses, retries=s.retries,
                react_last=s.react_last, react_avg=s.react_avg, confirm_last=s.confirm_last,
                last_event=acc.last_event, last_event_t=acc.last_event_t, error=acc.error))
        self._snap = EngineSnap(
            t=time.time(), accounts=tuple(snaps), tick_ms=self._tick_ms, work_ms=self._work_ms,
            capture_ms=self._cap_ms,
            rendering_ok=self._rendering_ok, exit_mode=self.settings.exit_mode,
            exit_in_progress=self._exit_t0 is not None, last_exit_ms=self._last_exit_ms,
            last_exit_count=self._last_exit_n, cpu=self._cpu, ram_mb=self._ram,
            clients_ram_mb=self._clients_ram,
            paused_all=bool(snaps) and all(a.state in ("PAUSED", "STOPPED") for a in snaps))
