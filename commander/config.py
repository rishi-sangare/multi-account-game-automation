"""Typed configuration: settings, accounts and presets (all plain JSON on disk)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from . import paths

# --------------------------------------------------------------------------- settings


class Layout(BaseModel):
    cols: int = Field(3, ge=1, le=10)
    client_w: int = Field(320, ge=160)
    client_h: int = Field(240, ge=120)
    gap: int = Field(4, ge=0)
    origin: tuple[int, int] = (0, 0)
    auto_tile: bool = True


class Hotkeys(BaseModel):
    exit_all: str = "ctrl+alt+x"
    kill_all: str = "ctrl+alt+k"
    pause_all: str = "ctrl+alt+p"


class Telegram(BaseModel):
    enabled: bool = False
    token: str = ""
    allowed_user_id: int = 0


ExitMode = Literal["leave_battle", "close_client", "kill"]
InputMode = Literal["post", "sendinput"]


class Settings(BaseModel):
    poll_ms: int = Field(40, ge=10, le=500)
    verify_ms: int = Field(220, ge=50, le=3000)
    max_retries: int = Field(3, ge=0, le=10)
    exit_mode: ExitMode = "leave_battle"
    auto_relaunch: bool = True
    rename_windows: bool = False
    raise_covered: bool = True          # bring a client back on top if another window covers it
    anti_afk: bool = False
    layout: Layout = Layout()
    hotkeys: Hotkeys = Hotkeys()
    telegram: Telegram = Telegram()


# --------------------------------------------------------------------------- accounts

_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")


class Account(BaseModel):
    id: str
    name: str
    enabled: bool = True
    mode: Literal["launch", "attach"] = "launch"
    exe: str = ""                      # "{mock}" = bundled simulator
    args: list[str] = []
    workdir: Optional[str] = None
    window_title: Optional[str] = None  # regex used by attach mode / re-binding
    preset: str = "mock-lobby"
    input_mode: InputMode = "post"
    slot: Optional[int] = None

    @field_validator("id")
    @classmethod
    def _id_ok(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError("id must be 1-32 chars: letters, digits, _ or -")
        return v


class AccountsFile(BaseModel):
    accounts: list[Account] = []

    @model_validator(mode="after")
    def _unique(self) -> "AccountsFile":
        ids = [a.id for a in self.accounts]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"duplicate account ids: {sorted(dup)}")
        return self


# --------------------------------------------------------------------------- presets


class Point(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class PixelCheck(BaseModel):
    at: Optional[str] = None          # name of a point …
    x: Optional[float] = None         # … or an inline ratio position
    y: Optional[float] = None
    rgb: tuple[int, int, int]
    tol: int = Field(18, ge=0, le=255)

    @model_validator(mode="after")
    def _where(self) -> "PixelCheck":
        if self.at is None and (self.x is None or self.y is None):
            raise ValueError("pixel check needs 'at' or both 'x' and 'y'")
        return self


class Trigger(BaseModel):
    type: Literal["pixels", "template", "not"]
    mode: Literal["all", "any"] = "all"            # pixels
    checks: list[PixelCheck] = []                  # pixels
    img: Optional[str] = None                      # template (file in config/templates)
    img_client_w: Optional[int] = None             # client width the template was captured at
    region: Optional[tuple[float, float, float, float]] = None  # template search area (ratios l,t,r,b)
    thr: float = 0.85                              # template
    of: Optional[str] = None                       # not
    debounce: int = Field(1, ge=1, le=10)

    @model_validator(mode="after")
    def _shape(self) -> "Trigger":
        if self.type == "pixels" and not self.checks:
            raise ValueError("pixels trigger needs at least one check")
        if self.type == "template" and (not self.img or not self.region):
            raise ValueError("template trigger needs 'img' and 'region'")
        if self.type == "not" and not self.of:
            raise ValueError("not trigger needs 'of'")
        return self


class Step(BaseModel):
    click: Optional[str] = None       # point name
    key: Optional[str] = None         # e.g. "ESC", "W", "ENTER"
    wait_ms: Optional[int] = None
    hold_ms: int = 0

    @model_validator(mode="after")
    def _one(self) -> "Step":
        if sum(v is not None for v in (self.click, self.key, self.wait_ms)) != 1:
            raise ValueError("each step needs exactly one of click / key / wait_ms")
        return self


class Timeouts(BaseModel):
    join_s: float = 6.0
    launch_s: float = 60.0
    connect_s: float = 90.0
    exit_retry_s: float = 1.2
    stuck_s: float = 120.0
    anti_afk_every_s: float = 120.0


REQUIRED_TRIGGERS = ("join_ready", "in_battle", "lobby")
REQUIRED_SEQUENCES = ("join", "exit")


class Preset(BaseModel):
    name: str = ""
    description: str = ""
    base_resolution: tuple[int, int] = (640, 480)
    points: dict[str, Point] = {}
    triggers: dict[str, Trigger] = {}
    sequences: dict[str, list[Step]] = {}
    timeouts: Timeouts = Timeouts()

    @model_validator(mode="after")
    def _refs(self) -> "Preset":
        missing = [t for t in REQUIRED_TRIGGERS if t not in self.triggers]
        if missing:
            raise ValueError(f"preset is missing triggers: {missing}")
        missing = [s for s in REQUIRED_SEQUENCES if s not in self.sequences]
        if missing:
            raise ValueError(f"preset is missing sequences: {missing}")
        for tname, trig in self.triggers.items():
            for c in trig.checks:
                if c.at is not None and c.at not in self.points:
                    raise ValueError(f"trigger '{tname}' references unknown point '{c.at}'")
            if trig.type == "not" and trig.of not in self.triggers:
                raise ValueError(f"trigger '{tname}' negates unknown trigger '{trig.of}'")
        for sname, steps in self.sequences.items():
            for st in steps:
                if st.click is not None and st.click not in self.points:
                    raise ValueError(f"sequence '{sname}' clicks unknown point '{st.click}'")
        return self


# --------------------------------------------------------------------------- io


class ConfigError(Exception):
    pass


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path.name}: invalid JSON at line {e.lineno}: {e.msg}") from e


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)                                   # atomic on Windows


def _validate(model, data: dict, label: str):
    try:
        return model.model_validate(data)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise ConfigError(f"{label}: {loc}: {first['msg']}") from e


def load_settings() -> Settings:
    if not paths.SETTINGS_FILE.exists():
        s = Settings()
        save_settings(s)
        return s
    return _validate(Settings, _read_json(paths.SETTINGS_FILE), "settings.json")


def save_settings(s: Settings) -> None:
    _write_json(paths.SETTINGS_FILE, s.model_dump(mode="json"))


def load_accounts() -> list[Account]:
    if not paths.ACCOUNTS_FILE.exists():
        save_accounts([])
        return []
    return _validate(AccountsFile, _read_json(paths.ACCOUNTS_FILE), "accounts.json").accounts


def save_accounts(accounts: list[Account]) -> None:
    _write_json(paths.ACCOUNTS_FILE, AccountsFile(accounts=accounts).model_dump(mode="json", exclude_none=True))


def preset_path(name: str) -> Path:
    return paths.PRESETS_DIR / f"{name}.json"


def list_presets() -> list[str]:
    return sorted(p.stem for p in paths.PRESETS_DIR.glob("*.json"))


def load_preset(name: str) -> Preset:
    p = preset_path(name)
    if not p.exists():
        raise ConfigError(f"preset '{name}' not found ({p.name})")
    preset = _validate(Preset, _read_json(p), p.name)
    preset.name = name
    return preset


def save_preset(preset: Preset) -> None:
    data = preset.model_dump(mode="json", exclude_none=True)
    data.pop("name", None)
    _write_json(preset_path(preset.name), data)


def presets_mtime() -> float:
    files = list(paths.PRESETS_DIR.glob("*.json")) + [paths.ACCOUNTS_FILE, paths.SETTINGS_FILE]
    return max((f.stat().st_mtime for f in files if f.exists()), default=0.0)
