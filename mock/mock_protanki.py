"""ProTanki mock client - a stand-in game window for testing and demos.

A real Win32 window (like the AIR client) that draws a ProTanki-style lobby, battle list, loading
screen, battle HUD and Esc menu. Battles open at random moments: the PLAY button turns green for a
short window, then the slot closes again. The window measures the *true* reaction time from the
moment green is on screen to the click arriving, and counts slots that were missed.

    python mock_protanki.py --name IronVolk [--size 640x480] [--pos 0,0] [--seed 7]
"""
from __future__ import annotations

import argparse
import ctypes
import random
import sys
import time
from ctypes import wintypes
from pathlib import Path

import win32api
import win32con
import win32gui
from PIL import Image, ImageDraw, ImageFont

BASE_W, BASE_H = 640, 480

# --------------------------------------------------------------------------- palette

BG = (15, 19, 17)
PANEL = (23, 29, 26)
PANEL_HI = (29, 36, 32)
LINE = (44, 54, 48)
TOPBAR = (31, 39, 35)
TOPLINE = (66, 82, 72)
TEXT = (214, 222, 216)
DIM = (128, 141, 133)
FAINT = (82, 94, 87)
GOLD = (228, 188, 92)
GREEN = (92, 190, 70)          # PLAY ready - the trigger colour
GREEN_DK = (18, 40, 14)
WAIT = (52, 60, 56)
FULL = (96, 52, 48)
ROW_SEL = (36, 50, 42)
BLUE_TEAM = (46, 112, 196)
RED_TEAM = (196, 64, 52)

MAPS = ["Sandbox", "Silence", "Island", "Kungur", "Polygon", "Rio", "Serpuhov", "Brest",
        "Highways", "Boombox", "Arena", "Molotov", "Duel", "Dusseldorf"]
MODES = ["DM", "TDM", "CTF", "CP"]

# --------------------------------------------------------------------------- fonts

_FONT_DIR = Path("C:/Windows/Fonts")


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for n in (name, "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(str(_FONT_DIR / n), size)
        except OSError:
            continue
    return ImageFont.load_default()


F_XS = _font("segoeui.ttf", 10)
F_S = _font("segoeui.ttf", 11)
F_M = _font("segoeuisb.ttf", 12)
F_B = _font("segoeuib.ttf", 13)
F_L = _font("segoeuib.ttf", 18)
F_XL = _font("segoeuib.ttf", 26)

# --------------------------------------------------------------------------- geometry (base coords)

R_TOPBAR = (0, 0, BASE_W, 34)
R_CHAT = (8, 42, 200, 472)
R_LIST = (208, 42, 452, 472)
R_INFO = (460, 42, 632, 472)
R_PLAY = (470, 410, 622, 452)
R_MENU = (220, 150, 420, 318)
R_RESUME = (240, 200, 400, 236)
R_EXIT = (240, 250, 400, 286)
NAV = [("BATTLES", 352), ("GARAGE", 418), ("SHOP", 476), ("SETTINGS", 524), ("EXIT", 594)]


def inside(r, x, y) -> bool:
    return r[0] <= x < r[2] and r[1] <= y < r[3]


# --------------------------------------------------------------------------- game model


class Game:
    def __init__(self, name: str, rng: random.Random, wait: tuple[float, float], slot_s: float,
                 battle: tuple[float, float]):
        self.name = name
        self.rng = rng
        self.wait = wait
        self.slot_s = slot_s
        self.battle_len = battle
        self.crystals = rng.randint(4000, 90000)
        self.rank = rng.randint(8, 24)
        self.chat = self._chat_lines()
        self.joins = 0
        self.missed = 0
        self.reacts: list[float] = []
        self.last_react: float | None = None
        self.state = "lobby"            # lobby | loading | battle | finished
        self.menu = False
        self._enter_lobby()

    # -- lobby

    def _enter_lobby(self) -> None:
        self.state = "lobby"
        self.menu = False
        self.battles = self._battle_list()
        self.sel = self.rng.randrange(len(self.battles))
        self.ready = False
        self.full_until = 0.0
        self.t_ready_shown: float | None = None
        self.t_open = time.perf_counter() + self.rng.uniform(*self.wait)

    def _battle_list(self) -> list[tuple[str, str, int, int]]:
        out = []
        for m in self.rng.sample(MAPS, 11):
            cap = self.rng.choice([8, 10, 12, 16, 20])
            out.append((m, self.rng.choice(MODES), self.rng.randint(cap // 2, cap), cap))
        return out

    def _chat_lines(self) -> list[tuple[str, str]]:
        names = ["kolya_tank", "Hornet88", "GreyWolf", "Sandstorm", "mamba", "Ruslan_T", "Tundra", "Kestrel"]
        msgs = ["gg", "anyone CTF on Island?", "gold soon", "lol", "need 1 for Kungur", "who has m3 rail",
                "+1", "nice shot", "join Silence", "brb", "rail op", "ty"]
        return [(self.rng.choice(names), self.rng.choice(msgs)) for _ in range(16)]

    # -- clock

    def update(self, now: float) -> bool:
        """Advance timers. Returns True when the picture changed."""
        if self.state == "lobby":
            if not self.ready and now >= self.t_open and now >= self.full_until:
                self.ready = True
                self.t_ready_shown = None                    # stamped at paint time
                m, mode, p, cap = self.battles[self.sel]
                self.battles[self.sel] = (m, mode, cap - 1, cap)
                return True
            if self.ready and self.t_ready_shown is not None and now - self.t_ready_shown > self.slot_s:
                self.ready = False
                self.missed += 1
                m, mode, p, cap = self.battles[self.sel]
                self.battles[self.sel] = (m, mode, cap, cap)
                self.full_until = now + 1.2
                self.t_open = now + self.rng.uniform(*self.wait)
                return True
            return False
        if self.state == "loading" and now >= self.t_state_end:
            self.state = "battle"
            self.t_battle_start = now
            self.t_state_end = now + self.rng.uniform(*self.battle_len)
            return True
        if self.state == "battle" and now >= self.t_state_end and not self.menu:
            self.state = "finished"
            self.t_state_end = now + 1.8
            return True
        if self.state == "finished" and now >= self.t_state_end:
            self._enter_lobby()
            return True
        return False

    # -- input

    def click(self, bx: float, by: float, now: float) -> bool:
        if self.state == "lobby":
            if inside(R_PLAY, bx, by) and self.ready:
                shown = self.t_ready_shown or now
                self.last_react = (now - shown) * 1000
                self.reacts.append(self.last_react)
                self.joins += 1
                self.ready = False
                self.state = "loading"
                self.t_state_end = now + self.rng.uniform(0.30, 0.55)
                return True
            if inside(R_LIST, bx, by):
                row = int((by - R_LIST[1] - 44) // 34)
                if 0 <= row < len(self.battles) and row != self.sel and not self.ready:
                    self.sel = row
                    return True
            return False
        if self.state == "battle" and self.menu:
            if inside(R_RESUME, bx, by):
                self.menu = False
                return True
            if inside(R_EXIT, bx, by):
                self._enter_lobby()
                return True
        return False

    def escape(self) -> bool:
        if self.state == "battle":
            self.menu = not self.menu
            return True
        return False

    # -- rendering

    def render(self, now: float) -> Image.Image:
        img = Image.new("RGB", (BASE_W, BASE_H), BG)
        d = ImageDraw.Draw(img)
        if self.state == "lobby":
            self._draw_lobby(d, now)
        elif self.state == "loading":
            self._draw_loading(d, now)
        else:
            self._draw_battle(img, d, now)
        return img

    def _draw_topbar(self, d: ImageDraw.ImageDraw) -> None:
        d.rectangle(R_TOPBAR, fill=TOPBAR)
        d.line((0, 34, BASE_W, 34), fill=TOPLINE)
        for i in range(3):                                   # rank chevrons
            y = 9 + i * 5
            d.polygon([(12, y), (19, y + 4), (26, y), (26, y + 3), (19, y + 7), (12, y + 3)], fill=GOLD)
        d.text((34, 8), self.name, font=F_B, fill=TEXT)
        d.text((34, 21), f"rank {self.rank}", font=F_XS, fill=DIM)
        cx = 168
        d.polygon([(cx, 17), (cx + 6, 10), (cx + 12, 17), (cx + 6, 24)], fill=GOLD)
        d.text((cx + 18, 9), f"{self.crystals:,}".replace(",", " "), font=F_M, fill=GOLD)
        for label, x in NAV:
            w = d.textlength(label, font=F_S)
            active = label == "BATTLES"
            if active:
                d.rectangle((x - 8, 0, x + w + 8, 33), fill=PANEL_HI)
                d.line((x - 8, 33, x + w + 8, 33), fill=GREEN, width=2)
            d.text((x, 10), label, font=F_S, fill=TEXT if active else DIM)

    def _draw_lobby(self, d: ImageDraw.ImageDraw, now: float) -> None:
        self._draw_topbar(d)
        # chat
        d.rectangle(R_CHAT, fill=PANEL, outline=LINE)
        d.text((R_CHAT[0] + 10, R_CHAT[1] + 8), "CHAT", font=F_M, fill=DIM)
        y = R_CHAT[1] + 32
        for who, msg in self.chat[:13]:
            d.text((R_CHAT[0] + 10, y), who, font=F_XS, fill=GOLD if who == "Hornet88" else (120, 170, 140))
            d.text((R_CHAT[0] + 10, y + 12), msg, font=F_S, fill=TEXT)
            y += 28
            if y > R_CHAT[3] - 70:
                break
        # mock stats strip (bottom of chat)
        sy = R_CHAT[3] - 46
        d.rectangle((R_CHAT[0] + 1, sy, R_CHAT[2] - 1, R_CHAT[3] - 1), fill=(18, 23, 21))
        d.line((R_CHAT[0] + 1, sy, R_CHAT[2] - 1, sy), fill=LINE)
        react = f"{self.last_react:.0f} ms" if self.last_react is not None else "—"
        d.text((R_CHAT[0] + 10, sy + 6), "LAST REACTION", font=F_XS, fill=FAINT)
        d.text((R_CHAT[0] + 10, sy + 19), react, font=F_L, fill=GREEN if self.last_react is not None else DIM)
        d.text((R_CHAT[0] + 108, sy + 6), f"joins  {self.joins}", font=F_XS, fill=DIM)
        d.text((R_CHAT[0] + 108, sy + 20), f"missed {self.missed}", font=F_XS,
               fill=(220, 110, 100) if self.missed else DIM)
        # battle list
        d.rectangle(R_LIST, fill=PANEL, outline=LINE)
        d.text((R_LIST[0] + 10, R_LIST[1] + 8), "BATTLES", font=F_M, fill=DIM)
        d.text((R_LIST[2] - 64, R_LIST[1] + 9), f"{len(self.battles)} open", font=F_XS, fill=FAINT)
        hy = R_LIST[1] + 30
        d.line((R_LIST[0] + 1, hy + 12, R_LIST[2] - 1, hy + 12), fill=LINE)
        d.text((R_LIST[0] + 12, hy - 1), "MAP", font=F_XS, fill=FAINT)
        d.text((R_LIST[0] + 128, hy - 1), "MODE", font=F_XS, fill=FAINT)
        d.text((R_LIST[0] + 182, hy - 1), "PLAYERS", font=F_XS, fill=FAINT)
        for i, (m, mode, p, cap) in enumerate(self.battles):
            ry = R_LIST[1] + 44 + i * 34
            if ry + 30 > R_LIST[3]:
                break
            if i == self.sel:
                d.rectangle((R_LIST[0] + 1, ry, R_LIST[2] - 1, ry + 32), fill=ROW_SEL)
                d.rectangle((R_LIST[0] + 1, ry, R_LIST[0] + 3, ry + 32), fill=GREEN)
            d.text((R_LIST[0] + 12, ry + 8), m, font=F_M, fill=TEXT)
            d.text((R_LIST[0] + 128, ry + 9), mode, font=F_S, fill=DIM)
            full = p >= cap
            d.text((R_LIST[0] + 182, ry + 9), f"{p}/{cap}", font=F_S,
                   fill=(200, 110, 100) if full else (150, 200, 150))
        # battle info
        d.rectangle(R_INFO, fill=PANEL, outline=LINE)
        m, mode, p, cap = self.battles[self.sel]
        self._draw_minimap(d, (R_INFO[0] + 10, R_INFO[1] + 10, R_INFO[2] - 10, R_INFO[1] + 120), m)
        x0 = R_INFO[0] + 12
        d.text((x0, R_INFO[1] + 130), m, font=F_L, fill=TEXT)
        d.text((x0, R_INFO[1] + 156), {"DM": "Deathmatch", "TDM": "Team deathmatch", "CTF": "Capture the flag",
                                       "CP": "Control points"}[mode], font=F_S, fill=DIM)
        rows = [("Players", f"{p} / {cap}"), ("Fund", f"{(hash(m) % 900 + 300) * 3}"),
                ("Time left", f"{(hash(m) % 12) + 3}:00"), ("Score limit", f"{(hash(m) % 5 + 3) * 10}")]
        for i, (k, v) in enumerate(rows):
            yy = R_INFO[1] + 186 + i * 26
            d.text((x0, yy), k, font=F_S, fill=FAINT)
            d.text((R_INFO[2] - 12 - d.textlength(v, font=F_M), yy - 1), v, font=F_M, fill=TEXT)
            d.line((x0, yy + 19, R_INFO[2] - 12, yy + 19), fill=(32, 40, 36))
        # PLAY button
        if self.ready:
            d.rectangle(R_PLAY, fill=GREEN)
            label, col = "PLAY", GREEN_DK
        elif now < self.full_until:
            d.rectangle(R_PLAY, fill=FULL)
            label, col = "FULL", (230, 190, 186)
        else:
            d.rectangle(R_PLAY, fill=WAIT)
            label, col = "WAITING FOR SLOT", DIM
        f = F_L if self.ready else F_S
        w = d.textlength(label, font=f)
        d.text(((R_PLAY[0] + R_PLAY[2] - w) / 2, R_PLAY[1] + (9 if self.ready else 13)), label, font=f, fill=col)

    def _draw_minimap(self, d: ImageDraw.ImageDraw, r, name: str) -> None:
        rr = random.Random(name)
        d.rectangle(r, fill=(58, 70, 52))
        for _ in range(9):
            x = rr.randint(r[0], r[2] - 30)
            y = rr.randint(r[1], r[3] - 20)
            d.rectangle((x, y, x + rr.randint(12, 40), y + rr.randint(8, 24)),
                        fill=rr.choice([(92, 96, 84), (110, 102, 80), (76, 86, 70)]))
        for gx in range(r[0], r[2], 20):
            d.line((gx, r[1], gx, r[3]), fill=(66, 78, 60))
        for gy in range(r[1], r[3], 20):
            d.line((r[0], gy, r[2], gy), fill=(66, 78, 60))
        d.rectangle(r, outline=LINE)

    def _draw_loading(self, d: ImageDraw.ImageDraw, now: float) -> None:
        d.rectangle((0, 0, BASE_W, BASE_H), fill=(10, 12, 11))
        m = self.battles[self.sel][0]
        w = d.textlength(m.upper(), font=F_XL)
        d.text(((BASE_W - w) / 2, 196), m.upper(), font=F_XL, fill=TEXT)
        d.text(((BASE_W - d.textlength("Entering battle", font=F_S)) / 2, 234), "Entering battle", font=F_S, fill=DIM)
        d.rectangle((220, 262, 420, 266), fill=(30, 36, 33))
        d.rectangle((220, 262, 220 + int(200 * min(1, 1 - (self.t_state_end - now) / 0.5)), 266), fill=GREEN)

    def _draw_battle(self, img: Image.Image, d: ImageDraw.ImageDraw, now: float) -> None:
        for y in range(0, BASE_H, 4):                          # sky -> ground
            t = y / BASE_H
            c = (int(70 + 18 * t), int(84 + 4 * t), int(92 - 34 * t)) if y < 230 else (86, 78, 58)
            d.rectangle((0, y, BASE_W, y + 4), fill=c)
        rr = random.Random(self.battles[self.sel][0])
        for _ in range(7):
            x = rr.randint(0, BASE_W - 80)
            h = rr.randint(40, 110)
            d.rectangle((x, 230 - h, x + rr.randint(40, 90), 232), fill=(64, 70, 66))
        d.polygon([(0, 300), (BASE_W, 280), (BASE_W, BASE_H), (0, BASE_H)], fill=(96, 86, 62))
        # tank
        cx, cy = 320, 352
        d.rectangle((cx - 46, cy - 6, cx + 46, cy + 18), fill=(70, 84, 60))
        d.rectangle((cx - 50, cy + 14, cx + 50, cy + 26), fill=(36, 40, 34))
        d.ellipse((cx - 22, cy - 22, cx + 22, cy + 6), fill=(84, 100, 70))
        d.rectangle((cx + 10, cy - 12, cx + 78, cy - 5), fill=(60, 72, 52))
        # HUD - team score plates (the in_battle trigger)
        d.rectangle((8, 8, 70, 36), fill=BLUE_TEAM)
        d.rectangle((70, 8, 132, 36), fill=RED_TEAM)
        d.text((20, 11), "12", font=F_L, fill=(240, 246, 255))
        d.text((90, 11), "9", font=F_L, fill=(255, 240, 238))
        left = max(0, int(self.t_state_end - now)) if self.state == "battle" else 0
        tm = f"{left // 60}:{left % 60:02d}"
        d.rectangle((280, 8, 360, 34), fill=(18, 22, 20))
        d.text((320 - d.textlength(tm, font=F_L) / 2, 9), tm, font=F_L, fill=TEXT)
        d.rectangle((220, 448, 420, 460), fill=(20, 24, 22))
        d.rectangle((222, 450, 222 + 150, 458), fill=GREEN)
        d.text((BASE_W - 112, BASE_H - 22), "Esc - menu", font=F_XS, fill=(210, 214, 208))
        if self.state == "finished":
            self._dim(img, d)
            d.rectangle((200, 190, 440, 270), fill=PANEL, outline=LINE)
            t = "BATTLE FINISHED"
            d.text((320 - d.textlength(t, font=F_L) / 2, 210), t, font=F_L, fill=TEXT)
            d.text((320 - d.textlength("returning to lobby…", font=F_S) / 2, 238), "returning to lobby…",
                   font=F_S, fill=DIM)
        elif self.menu:
            self._dim(img, d)
            d.rectangle(R_MENU, fill=PANEL, outline=LINE)
            d.text((R_MENU[0] + 20, R_MENU[1] + 16), "MENU", font=F_M, fill=DIM)
            d.rectangle(R_RESUME, fill=PANEL_HI, outline=LINE)
            d.text((320 - d.textlength("RESUME", font=F_B) / 2, R_RESUME[1] + 9), "RESUME", font=F_B, fill=TEXT)
            d.rectangle(R_EXIT, fill=(120, 44, 38))
            d.text((320 - d.textlength("EXIT BATTLE", font=F_B) / 2, R_EXIT[1] + 9), "EXIT BATTLE", font=F_B,
                   fill=(255, 232, 228))

    @staticmethod
    def _dim(img: Image.Image, d: ImageDraw.ImageDraw) -> None:
        overlay = Image.new("RGB", img.size, (0, 0, 0))
        img.paste(Image.blend(img, overlay, 0.55))


# --------------------------------------------------------------------------- Win32 window

gdi32 = ctypes.windll.gdi32
user32 = ctypes.windll.user32


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class MockWindow:
    CLASS = "ProTankiMockWindow"
    TIMER_MS = 10

    def __init__(self, game: Game, size: tuple[int, int], pos: tuple[int, int] | None, strict: bool = False):
        self.game = game
        self.strict = strict              # ignore synthetic (posted) clicks, like some game engines do
        self.real_down = False
        self.frame: Image.Image | None = None
        self.dirty = True
        self.next_second = 0.0
        hinst = win32api.GetModuleHandle(None)
        wc = win32gui.WNDCLASS()
        wc.hInstance = hinst
        wc.lpszClassName = self.CLASS
        wc.lpfnWndProc = self.wndproc
        wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
        wc.style = win32con.CS_HREDRAW | win32con.CS_VREDRAW
        try:
            win32gui.RegisterClass(wc)
        except win32gui.error:
            pass
        style = win32con.WS_OVERLAPPEDWINDOW & ~win32con.WS_MAXIMIZEBOX
        rc = wintypes.RECT(0, 0, size[0], size[1])
        user32.AdjustWindowRectEx(ctypes.byref(rc), style & 0xFFFFFFFF, False, 0)
        x, y = pos if pos else (win32con.CW_USEDEFAULT, win32con.CW_USEDEFAULT)
        self.hwnd = win32gui.CreateWindow(self.CLASS, f"ProTanki — {game.name}", style, x, y,
                                          rc.right - rc.left, rc.bottom - rc.top, 0, 0, hinst, None)
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNOACTIVATE)
        user32.SetTimer(self.hwnd, 1, self.TIMER_MS, None)

    def _client(self) -> tuple[int, int]:
        l, t, r, b = win32gui.GetClientRect(self.hwnd)
        return max(r - l, 1), max(b - t, 1)

    def _to_base(self, lparam: int) -> tuple[float, float]:
        x = ctypes.c_short(lparam & 0xFFFF).value
        y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        cw, ch = self._client()
        return x * BASE_W / cw, y * BASE_H / ch

    def wndproc(self, hwnd, msg, wparam, lparam):
        g = self.game
        if msg == win32con.WM_TIMER:
            now = time.perf_counter()
            changed = g.update(now)
            if now >= self.next_second:                       # clocks / timers
                self.next_second = now + 0.25
                changed = changed or g.state in ("battle", "loading")
            if g.state == "loading":
                changed = True
            if changed:
                self.dirty = True
                win32gui.InvalidateRect(hwnd, None, False)
            return 0
        if msg == win32con.WM_LBUTTONDOWN:
            # A posted message does not update the thread's key state; real input does.
            self.real_down = bool(user32.GetKeyState(win32con.VK_LBUTTON) & 0x8000)
            return 0
        if msg == win32con.WM_LBUTTONUP:
            if self.strict and not self.real_down:
                return 0
            self.real_down = False
            bx, by = self._to_base(lparam)
            if g.click(bx, by, time.perf_counter()):
                self.dirty = True
                win32gui.InvalidateRect(hwnd, None, False)
            return 0
        if msg == win32con.WM_KEYDOWN:
            if wparam == win32con.VK_ESCAPE and g.escape():
                self.dirty = True
                win32gui.InvalidateRect(hwnd, None, False)
            return 0
        if msg == win32con.WM_PAINT:
            self._paint(hwnd)
            return 0
        if msg == win32con.WM_ERASEBKGND:
            return 1
        if msg == win32con.WM_SYSCOMMAND and (wparam & 0xFFF0) == win32con.SC_KEYMENU:
            return 0                                          # no Alt menu-mode, like a game window
        if msg == win32con.WM_SIZE:
            self.dirty = True
            win32gui.InvalidateRect(hwnd, None, False)
            return 0
        if msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _paint(self, hwnd) -> None:
        hdc, ps = win32gui.BeginPaint(hwnd)
        try:
            cw, ch = self._client()
            if self.dirty or self.frame is None or self.frame.size != (cw, ch):
                now = time.perf_counter()
                img = self.game.render(now)
                if (cw, ch) != (BASE_W, BASE_H):
                    img = img.resize((cw, ch), Image.BILINEAR)
                self.frame = img
                self.dirty = False
            raw = self.frame.tobytes("raw", "BGRX")
            bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), cw, -ch, 1, 32, 0, 0, 0, 0, 0, 0)
            gdi32.StretchDIBits(hdc, 0, 0, cw, ch, 0, 0, cw, ch, raw, ctypes.byref(bih), 0, 0x00CC0020)
            g = self.game
            if g.state == "lobby" and g.ready and g.t_ready_shown is None:
                g.t_ready_shown = time.perf_counter()          # green is on screen *now*
        finally:
            win32gui.EndPaint(hwnd, ps)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="Tanker01")
    ap.add_argument("--size", default="640x480")
    ap.add_argument("--pos", default=None, help="x,y of the window")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--wait", default="4,16", help="seconds until a slot opens: min,max")
    ap.add_argument("--slot", type=float, default=2.5, help="seconds a slot stays open")
    ap.add_argument("--battle", default="12,30", help="battle length seconds: min,max")
    ap.add_argument("--strict-input", action="store_true",
                    help="ignore background (posted) clicks - simulates engines that need real input")
    a = ap.parse_args()

    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass
    w, h = (int(v) for v in a.size.lower().split("x"))
    pos = tuple(int(v) for v in a.pos.split(",")) if a.pos else None
    rng = random.Random(a.seed if a.seed is not None else hash(a.name) ^ int(time.time()))
    wait = tuple(float(v) for v in a.wait.split(","))
    battle = tuple(float(v) for v in a.battle.split(","))
    game = Game(a.name, rng, wait, a.slot, battle)
    MockWindow(game, (w, h), pos, strict=a.strict_input)
    win32gui.PumpMessages()
    return 0


if __name__ == "__main__":
    sys.exit(main())
