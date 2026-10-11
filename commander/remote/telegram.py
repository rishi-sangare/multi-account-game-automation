"""Telegram remote control (optional): /exitall from your phone, /status, /screenshot.

Only the configured user id is obeyed. Uses the plain Bot API over HTTPS long-polling (no extra
dependencies). The token stays on this machine.
"""
from __future__ import annotations

import io
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .. import imaging
from ..win32 import capture

log = logging.getLogger("telegram")

HELP = ("ProTanki Commander\n"
        "/status - every account's state\n"
        "/exitall - leave battle on all accounts\n"
        "/kill - kill every client process\n"
        "/pause - pause automation · /resume - start again\n"
        "/screenshot - current desktop")


class TelegramBot(threading.Thread):
    def __init__(self, token: str, allowed_user_id: int, engine):
        super().__init__(name="telegram", daemon=True)
        self.token = token
        self.allowed = allowed_user_id
        self.engine = engine
        self.stop_evt = threading.Event()
        self.offset = 0
        self.base = f"https://api.telegram.org/bot{token}/"

    def stop(self) -> None:
        self.stop_evt.set()

    # -- http
    def _call(self, method: str, params: dict | None = None, timeout: float = 35) -> dict:
        data = urllib.parse.urlencode(params or {}).encode()
        with urllib.request.urlopen(self.base + method, data=data, timeout=timeout) as r:
            return json.loads(r.read().decode())

    def _send(self, chat_id: int, text: str) -> None:
        try:
            self._call("sendMessage", {"chat_id": chat_id, "text": text}, timeout=15)
        except Exception as e:
            log.warning("telegram send failed: %s", e)

    def _send_photo(self, chat_id: int, png: bytes, caption: str) -> None:
        boundary = uuid.uuid4().hex
        body = io.BytesIO()
        for k, v in (("chat_id", str(chat_id)), ("caption", caption)):
            body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
        body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"screen.png\"\r\n"
                   f"Content-Type: image/png\r\n\r\n".encode())
        body.write(png)
        body.write(f"\r\n--{boundary}--\r\n".encode())
        req = urllib.request.Request(self.base + "sendPhoto", data=body.getvalue(),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            urllib.request.urlopen(req, timeout=30).read()
        except Exception as e:
            log.warning("telegram photo failed: %s", e)

    # -- loop
    def run(self) -> None:
        try:
            me = self._call("getMe", timeout=15)
            log.info("telegram remote online as @%s (user %s only)", me["result"].get("username"), self.allowed)
        except Exception as e:
            log.error("telegram: cannot connect (%s) - check the bot token", e)
            return
        while not self.stop_evt.is_set():
            try:
                res = self._call("getUpdates", {"timeout": 25, "offset": self.offset}, timeout=35)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                log.debug("telegram poll error: %s", e)
                self.stop_evt.wait(3)
                continue
            for upd in res.get("result", []):
                self.offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                user = (msg.get("from") or {}).get("id")
                text = (msg.get("text") or "").strip().split("@")[0].lower()
                chat = (msg.get("chat") or {}).get("id")
                if not chat or not text:
                    continue
                if user != self.allowed:
                    log.warning("telegram: ignored command from unknown user %s", user)
                    continue
                self._handle(chat, text)

    def _handle(self, chat: int, cmd: str) -> None:
        log.info("telegram command %s", cmd)
        if cmd in ("/exitall", "/exit"):
            self.engine.submit("EXIT_ALL")
            time.sleep(1.2)
            snap = self.engine.snapshot()
            ms = f"{snap.last_exit_ms:.0f} ms" if snap.last_exit_ms is not None else "in progress"
            self._send(chat, f"EXIT ALL sent ({ms}).\n" + self._status_text())
        elif cmd == "/kill":
            self.engine.submit("KILL_ALL")
            self._send(chat, "Killing every client process.")
        elif cmd == "/pause":
            self.engine.submit("PAUSE")
            self._send(chat, "Automation paused.")
        elif cmd in ("/resume", "/startall"):
            self.engine.submit("START")
            self._send(chat, "Automation started.")
        elif cmd == "/status":
            self._send(chat, self._status_text())
        elif cmd == "/screenshot":
            img, _, _ = capture.grab_full()
            self._send_photo(chat, imaging.png_bytes(img), self._status_text()[:900])
        else:
            self._send(chat, HELP)

    def _status_text(self) -> str:
        snap = self.engine.snapshot()
        lines = []
        for a in snap.accounts:
            lines.append(f"{a.name:<12} {a.state.lower():<10} joins {a.joins}  missed {a.misses}")
        joins = sum(a.joins for a in snap.accounts)
        return f"{len(snap.accounts)} accounts · {joins} joins\n" + "\n".join(lines)
