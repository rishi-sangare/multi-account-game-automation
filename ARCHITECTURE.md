# ProTanki Multi-Account Clicker: Research and Architecture

> What this is: the research findings and the build plan for the proof-of-concept (POC) we show in the Loom video before we apply.
> Labels: **[C]** = a source confirms it · **[I]** = inferred · **[TEST]** = must be checked on a real install (Phase 0).

---

## 1. What the client is really asking for

| Job post says | What it means technically |
|---|---|
| "Control 6–10 ProTanki accounts on a VPS" | 6–10 game client windows on one Windows Server desktop, each logged into a different account |
| "Auto-click specified in-game buttons without missing actions" | Clicks at configurable points in each window, checked afterwards (verify-after-click and retry) |
| "Join battles immediately when the queue opens" | Watch each window for a visual signal and click Join within about 100 ms. **ProTanki has no matchmaking queue; it has a battle list [C].** "Queue opens" probably means a battle appears or gets free slots, the green **Play** button lights up, or a gold-box event starts. **Ask the client.** |
| "Exit all accounts instantly when triggered" | A global hotkey plus a big UI button: all windows leave the battle in parallel, and the client is force-closed if needed |
| "Set/change click coordinates, save presets" | A coordinate picker (click on the game window to record the point and its colour) and presets saved as JSON |
| "Run all instances concurrently without crashing or slowing the VPS" | One lightweight process, one capture loop, no busy-waiting, a watchdog that relaunches crashed clients |
| "Single executable for a fresh Windows VPS" | A Nuitka or PyInstaller build that needs no Python install |
| "Setup instructions for adding/removing accounts" | `accounts.json` plus an "Add account" wizard in the UI, and a SETUP.md |
| "Live demo" | The Loom now, and a live session on their VPS later |

**Likely real-world use [I]:** filling a battle with their own accounts when a gold box drops (worth 1k–100k crystals), then leaving all at once. Or farming battle funds, or avoiding the 5-minute AFK kick.

---

## 2. Research findings about the game

- **[C] ProTanki is alive.** It is a fan revival of Tanki Online (2014–2017 era).
  - Site: https://pro-tanki.online. The old `protanki-online.com` domain is now a parking page.
  - Discord has about 40k members, and there were news posts in Sept 2026.
- **[C] Windows client:** installer at `https://pro-tanki.online/assets/download/protanki-installer.exe`.
  - It installs to `C:\Program Files (x86)\ProTanki Online\ProTanki.exe` (Inno Setup installer).
- **[C] Technology:** Flash/ActionScript (`Prelauncher.swf` → `Loader.swf` → `library.swf`).
  - **[I]** It is packaged as an **Adobe AIR captive-runtime app** (a small 167 KB stub exe), so not Electron and not a browser.
  - It uses Stage3D/DirectX rendering, with a software fallback when there is no GPU.
- **[C] Multi-instance problem:** AIR allows **one instance per application ID**. Launching it a second time only focuses the first window.
  - The community launcher (`protanki-re/client-launcher`) gives each client profile its own App ID.
  - **[TEST]** Fix: **one copy of the install folder per account**, each with a unique `<id>` in `META-INF\AIR\application.xml`.
  - Fallbacks: Sandboxie, or a separate Windows user per account.
- **[C] Lobby UI:** top bar (Battles list, Garage, Settings, Fullscreen, Logout …), chat on the left, **battle list** in the centre.
  - Joinable battles show a **green "Play" button**, and you can pick a team.
  - Since 13.05.2026 the client remembers several accounts, which helps with login.
- **[C] Other rules that matter for automation:**
  - AFK kick after 5 minutes.
  - Battle-fund payouts only go to players still present when the battle ends.
  - Gold boxes give a siren and warning, then the drop 30–50 s later.
- **[C] No existing multibox or clicker tool for ProTanki was found** on GitHub, YouTube or forums, so we would be first.
- **[C] Game rules** (https://pro-tanki.online/en/game-rules):
  - bots and cheats are banned (1.1);
  - third-party programs used for advantage are banned (3.4);
  - battles between your own accounts are banned (3.6).
  - **This is the client's risk, not ours.** Put one neutral line about it in the proposal. Build no detection-evasion features.

---

## 3. Key technical decisions

### 3.1 Stack: Python 3.12, one process
| Concern | Choice | Why |
|---|---|---|
| Language | **Python 3.12** | Fastest to build (1–2 days). `pywin32`/`ctypes` give full Win32 access. |
| UI | **CustomTkinter** | Dark, modern, light on RAM, no extra binaries. Records well on Loom. |
| Clicks | **PostMessage to the deepest child HWND** (primary), **SendInput** under a global lock (fallback) | Background clicks run in parallel without focus. The fallback covers the case where AIR ignores posted messages. |
| Detection | **mss**: one desktop grab per tick, sliced into each window's ROI (region of interest) with **numpy** | One capture for all 10 windows per tick. Checking a pixel takes microseconds. |
| Template matching | `opencv-python-headless` (optional) | For triggers where a single pixel colour isn't reliable enough |
| Fallback capture | `PrintWindow(PW_CLIENTONLY \| PW_RENDERFULLCONTENT)` per HWND | For windows that overlap or are covered |
| Process control | `psutil` | Track PIDs, measure RAM/CPU, kill |
| Hotkey | Win32 `RegisterHotKey` on its own message-loop thread | Works when the tool isn't focused. No keyboard hook, so less AV suspicion. |
| Packaging | **Nuitka `--standalone`**, shipped as a zip (fewest AV flags). Fallback: PyInstaller `--onefile --noupx` | Runs on a fresh Windows Server 2019/2022 |
| Remote trigger (bonus) | Telegram bot `/exitall` `/status` `/screenshot` | Lets the client fire Exit All from a phone. A strong selling point. |

### 3.2 Why not the other options
- **C#/.NET:** NativeAOT doesn't support WinForms, and a self-contained build is about 66 MB or more. It is also slower for us to build.
- **AutoHotkey:** runs on one thread, has a basic GUI, and compiled AHK is often flagged by AV.
- **Chrome DevTools Protocol:** only works if the client is Chromium. It is Flash/AIR, so it doesn't apply.

---

## 4. System architecture

```
┌──────────────────────────────── ProTankiCommander.exe (1 process) ────────────────────────────────┐
│                                                                                                    │
│  UI thread (CustomTkinter)            Hotkey thread              Telegram thread (optional)        │
│  ├ Account table + state badges       RegisterHotKey             /exitall /status /screenshot      │
│  ├ Start/Stop/EXIT ALL                Ctrl+Alt+X → EXIT_ALL      ──────────┐                       │
│  ├ Coordinate picker overlay          Ctrl+Alt+P → PAUSE_ALL               │                       │
│  ├ Preset editor / Add-account wizard          │                           │                       │
│  └ Live log + CPU/RAM meter                    ▼                           ▼                       │
│            │  commands          ┌──────────── Command Bus (queue.Queue) ─────────────┐             │
│            └───────────────────►│  START / STOP / EXIT_ALL / RELOAD_PRESET / PICK    │             │
│                                 └────────────────────────┬───────────────────────────┘             │
│                                                          ▼                                         │
│   ┌──────────────────────────── Engine / Scheduler thread (tick 30–75 ms) ─────────────────────┐   │
│   │ 1. Capture:   mss grab of whole desktop (1x)  → slice ROI per account   (fallback PrintWindow)│   │
│   │ 2. Evaluate:  triggers per account (pixel / pixel_all / template) w/ 2-frame debounce        │   │
│   │ 3. Advance:   AccountStateMachine[i].step(observations)                                      │   │
│   │ 4. Dispatch:  actions → InputDispatcher                                                      │   │
│   │ 5. Watchdog:  IsWindow / pid alive / stuck-state timer → relaunch / recover                  │   │
│   └───────────────────────────────────────────┬──────────────────────────────────────────────────┘   │
│                                               ▼                                                    │
│   InputDispatcher:  PostMessageBackend  (parallel, µs, no focus)                                   │
│                     SendInputBackend    (single worker thread + global lock, focus→click→restore)  │
│                                                                                                    │
│   WindowManager:  launch → PID→HWND bind → SetWindowText("PT-acc1") → tile (SetWindowPos)          │
│   Launcher:       per-account install copy w/ unique AIR app-id                                    │
│   Config:         accounts.json + presets/*.json (validated, hot-reload)  · Logger: rotating file  │
└────────────────────────────────────────────────────────────────────────────────────────────────────┘
          │ PostMessage / SendInput                         ▲ pixels
          ▼                                                 │
   ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐   ← ProTanki.exe ×N, tiled grid,
   │ acc1   │ │ acc2   │ │ acc3   │ │ acc4   │ │ acc5   │ │ acc6   │     fixed client size, never
   └────────┘ └────────┘ └────────┘ └────────┘ └────────┘ └────────┘     minimized, not overlapping
```

**Why one scheduler thread instead of 10 worker threads:**
- one screen capture per tick instead of ten;
- no threads competing for the CPU;
- the timing is deterministic;
- the debug log is easy to read.

Posted clicks take microseconds, so dispatching them inline is effectively parallel. Only the SendInput fallback is serialized.

### 4.1 Account state machine
```
LAUNCH ──window found──► LOGIN? ──lobby trigger──► LOBBY ──open battle list──► WATCH
   ▲                                                  ▲                          │ trigger true 2 frames
   │ pid dead / hwnd lost (watchdog)                  │ battle ended             ▼
   └──────────────────────────────────────────── IN_BATTLE ◄──in_battle seen── JOIN (click Play)
                                                      │                          │ timeout → WATCH (miss++)
                          EXIT_ALL (any state, top priority)                     │
                                    ▼
             EXITING: run exit sequence (all accounts in parallel) → WM_CLOSE → kill after 2 s → EXITED
```

| State | Entry action | Exits when | Timeout / failure handling |
|---|---|---|---|
| LAUNCH | Start the process, bind the PID, tile the window | HWND found | 60 s → retry ×3 → ERROR |
| LOGIN (opt) | Login sequence (or saved account) | `lobby` trigger | 30 s → restart client |
| LOBBY | Click "Battles" | List visible | Window lost → LAUNCH |
| WATCH | Poll `join_ready` | true for 2 frames in a row | none (waits) |
| JOIN | Run the `join` sequence | `in_battle` | `join_s` → WATCH, misses++ |
| IN_BATTLE | Optional anti-AFK click every N s | `lobby` seen again | Window lost → LAUNCH |
| EXITING | Exit sequence → WM_CLOSE → TerminateProcess | Process gone | Hard kill at 2 s |

**"Never miss a click" guarantee:**
1. Every action is followed by a verify step: wait 80–200 ms, then check the expected next state.
2. If the check fails, retry with backoff, up to 3 times.
3. Then escalate: switch that account to the SendInput backend and send a Telegram alert.
4. Each click is logged as: account, action, coordinates, state before and after, latency.
5. When a verify fails, the region screenshot is saved as a PNG.

### 4.2 Coordinates and windows
- The process is made **per-monitor DPI-aware** at startup (`SetProcessDpiAwarenessContext(-4)`).
- Every client window gets a **fixed client size**: outer size from `AdjustWindowRectEx`, then `SetWindowPos` into its grid slot. Windows never overlap and are never minimized.
- Points are stored **relative to the client area, as ratios** (`x/cw`, `y/ch`), plus a `base_resolution`. One preset then works for every slot.
- PostMessage uses client coordinates of the **deepest child window** at that point (`ChildWindowFromPointEx` loop). Screen coordinates are only used for ROI slicing and the SendInput fallback.

### 4.3 Exit All (several layers, fastest first)
1. A global `threading.Event` that every state check and every wait looks at.
2. Phase A, graceful: post the preset's exit sequence (e.g. `Esc` → "Exit battle") to **all windows in the same tick**.
3. Phase B: `WM_CLOSE` to every HWND.
4. Phase C, after 2 s: `psutil.Process(pid).kill()` for every tracked PID, including children.
5. Target: **every account gone in under 1 s** (Phase A) or under 2.5 s worst case. The log prints the measured time; that's the Loom moment.
6. Triggers: the hotkey **Ctrl+Alt+X**, the red UI button, Telegram `/exitall`, and optionally a local HTTP endpoint on 127.0.0.1.

### 4.4 VPS operations (the trap most freelancers miss)
- **Minimized RDP window:** Windows stops rendering, so pixel checks go black and SendInput fails.
  - Ship `fix_rdp_minimized.reg` for the client's **own PC**: `RemoteDesktop_SuppressWhenMinimized=2` under `HKCU\Software\Microsoft\Terminal Server Client`, and the same under `HKLM` and `Wow6432Node`.
- **Disconnected RDP:** the session locks.
  - Ship `Disconnect-Keep-Running.bat`, which runs `tscon <id> /dest:console` as admin. The tool also checks for a locked session and warns.
- **Console falls back to 1024×768 after tscon:**
  - The tool forces the resolution at startup with `ChangeDisplaySettingsEx`.
  - Optionally install Virtual Display Driver (`winget install VirtualDrivers.Virtual-Display-Driver`).
- **Budget:** about 300–600 MB RAM per client [I], and CPU-heavy without a GPU.
  - Recommend at least 8 vCPU and 8–16 GB for 10 clients. Benchmark in Phase 0.
  - Turn graphics quality in the game settings to the minimum.

---

## 5. Config files

`config/accounts.json`
```json
{
  "layout": { "cols": 4, "client_w": 640, "client_h": 480, "gap": 4, "origin": [0, 0] },
  "hotkeys": { "exit_all": "ctrl+alt+x", "pause_all": "ctrl+alt+p" },
  "telegram": { "enabled": false, "token": "", "allowed_user_id": 0 },
  "accounts": [
    { "id": "acc1", "name": "Tanker01", "enabled": true,
      "install_dir": "C:/ProTanki/acc1", "exe": "ProTanki.exe", "args": [],
      "preset": "gold-rush", "input_mode": "post", "slot": 0 }
  ]
}
```

`config/presets/gold-rush.json` (coordinates are client-area ratios)
```json
{
  "base_resolution": [640, 480], "poll_ms": 40,
  "points": {
    "battles_btn": { "x": 0.512, "y": 0.031 },
    "play_btn":    { "x": 0.842, "y": 0.905 },
    "exit_btn":    { "x": 0.500, "y": 0.560 }
  },
  "triggers": {
    "join_ready": { "type": "pixel", "at": "play_btn", "rgb": [96, 200, 64], "tol": 18 },
    "in_battle":  { "type": "template", "img": "templates/hud.png", "region": [0, 0, 0.3, 0.1], "thr": 0.85 },
    "lobby":      { "type": "pixel_all", "checks": [ { "x": 0.1, "y": 0.1, "rgb": [30, 30, 30], "tol": 10 } ] }
  },
  "sequences": {
    "open_list": [ { "click": "battles_btn" } ],
    "join":      [ { "click": "play_btn" } ],
    "exit":      [ { "key": "ESC" }, { "wait_ms": 150 }, { "click": "exit_btn" } ],
    "anti_afk":  [ { "key": "W", "hold_ms": 80 } ]
  },
  "timeouts": { "join_s": 6, "launch_s": 60, "anti_afk_every_s": 120 }
}
```
- Both files are validated on load with pydantic or dataclasses, and reloaded when they change on disk.
- To add an account: copy the install folder, add one JSON entry (or use the wizard), and click Start.
- To remove an account: set `enabled:false` or delete the entry.

---

## 6. Project layout

```
protanki-commander/
├─ app/
│  ├─ main.py                 # entry: DPI aware, load config, start threads, UI mainloop
│  ├─ config.py               # pydantic models, load/save/hot-reload
│  ├─ engine.py               # scheduler tick loop, command bus, EXIT_ALL event
│  ├─ account.py              # AccountStateMachine
│  ├─ triggers.py             # pixel / pixel_all / template evaluators + debounce
│  ├─ sequences.py            # run click/key/wait steps via InputDispatcher
│  ├─ win/
│  │  ├─ dpi.py  windows.py   # enum/bind/tile/rename, client↔screen, deepest child
│  │  ├─ input_post.py        # PostMessage backend (mouse + key)
│  │  ├─ input_send.py        # SendInput backend (serialized, focus restore)
│  │  ├─ capture.py           # mss desktop grab + PrintWindow fallback
│  │  ├─ launcher.py          # per-account install copy, unique AIR app-id patcher
│  │  └─ hotkeys.py           # RegisterHotKey thread
│  ├─ remote/telegram_bot.py  # optional
│  └─ ui/ dashboard.py  picker.py  wizard.py  log_view.py
├─ config/ accounts.json  presets/*.json  templates/*.png
├─ tools/
│  ├─ mock_protanki.py        # demo simulator window (see §8)
│  ├─ spike_postmessage.py    # Phase-0 tests
│  ├─ spike_capture.py
│  └─ clone_install.py        # copy ProTanki dir N times + patch app-id
├─ vps/ fix_rdp_minimized.reg  Disconnect-Keep-Running.bat  SETUP.md
├─ build.ps1                  # nuitka --standalone --enable-plugin=tk-inter ...
└─ requirements.txt           # pywin32 customtkinter mss numpy psutil pydantic opencv-python-headless
```

---

## 7. Implementation plan (POC before applying)

### Phase 0: de-risking spike (2–3 h). Do this first; it decides everything.
Install ProTanki on your PC with 2–3 of your own free accounts, then answer:

| # | Test | How | Decides |
|---|---|---|---|
| 1 | Does a second instance start? | Run `ProTanki.exe` twice; then copy the folder and patch the app-id in `META-INF\AIR\application.xml` | Launcher strategy |
| 2 | Window class names | Spy++ / WinSpy / `win32gui.EnumChildWindows` dump | Which HWND gets the clicks |
| 3 | Is a posted click accepted while unfocused? | `spike_postmessage.py`: post a click on a lobby button of a background window | `input_mode` default |
| 4 | Do captures of overlapped or background windows come out black? | `spike_capture.py`: mss vs PrintWindow(3) | Capture backend |
| 5 | Can the window be resized to 640×480, and does the UI scale or crop? | SetWindowPos plus screenshots | Layout and ratio coordinates |
| 6 | RAM/CPU per instance | Task Manager / psutil | VPS sizing advice |
| 7 | How is a battle exited? | Play manually | Exit sequence |

### Phase 1: core engine (≈ 1 day)
config → windows (bind, tile) → input backends → capture plus triggers → state machine → engine tick → EXIT_ALL. CLI first, with logs.

### Phase 2: UI (≈ 0.5–1 day)
Dashboard table, the EXIT ALL button and hotkey, the coordinate picker (overlay, then click, then save the point and its RGB as a ratio), preset dropdown, log pane, CPU/RAM meter.

### Phase 3: demo polish (≈ 0.5 day)
`mock_protanki.py`, the reaction-time readout, Telegram `/exitall`, Nuitka build, and a VirusTotal check.

### Phase 4: optional
A one-hour test on a cheap Windows VPS (Vultr or Contabo, billed by the hour): unzip → run → RDP minimize and tscon test. This gives the 20-second "fresh VPS" clip.

---

## 8. Demo strategy: real game plus mock simulator

**Real ProTanki (2–3 windows, your accounts):**
- Proves it works on the actual AIR client.
- Shows the coordinate picker on the real green Play button.
- Shows a real join and a real Exit All.

**`mock_protanki.py` (6–8 windows):**
- A roughly 150-line Tkinter window that copies the lobby layout: a battle list and a green "PLAY" button that appears at a random time (3–20 s).
- It moves to an "IN BATTLE" screen with a timer, and has an Esc → Exit menu.
- It **measures the reaction time** from the button appearing to the click, and it counts missed clicks.
- It scales the demo to 10 accounts without 10 real clients, and it is deterministic on camera.
- Say on camera what it is ("a simulator I built to stress-test 10 accounts"). Being transparent builds trust.

Result on screen: **"10 accounts · average join 60–90 ms · 0 missed · Exit All in 0.4 s · 110 MB RAM"**.

Using the bot on real accounts breaks ProTanki's rules, so your demo accounts could be banned. Use throwaway accounts.

---

## 9. Risks and mitigations (short version goes in the proposal)

| Risk | Mitigation |
|---|---|
| AIR single-instance lock | Separate install copy per account with a patched app-id (`clone_install.py`). Fallback: Sandboxie or separate users |
| Background clicks ignored by AIR | Automatic fallback to serialized SendInput (about 40 ms per click, ~0.4 s for 10 accounts) |
| Minimized or disconnected RDP stops rendering | `.reg` fix, tscon disconnect script, startup check and warning |
| Resolution or DPI changes | DPI-aware process, fixed client size per slot, ratio coordinates |
| Game update moves buttons | Presets are data; re-pick in 1 minute. Template triggers tolerate small shifts. |
| AV or SmartScreen flags the exe | Nuitka standalone with no UPX; VirusTotal before delivery; source handed over |
| VPS overload | Single process, sleep-paced loop, ROI-only checks; recommend low graphics and VPS sizing |
| ToS bans | The client's decision. No evasion features. One neutral line in the proposal. |
