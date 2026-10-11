"""Filesystem locations. Works both from source and from a frozen (PyInstaller) build."""
from __future__ import annotations

import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent

CONFIG_DIR = ROOT / "config"
PRESETS_DIR = CONFIG_DIR / "presets"
TEMPLATES_DIR = CONFIG_DIR / "templates"
LOGS_DIR = ROOT / "logs"
FAIL_DIR = LOGS_DIR / "fail"

SETTINGS_FILE = CONFIG_DIR / "settings.json"
ACCOUNTS_FILE = CONFIG_DIR / "accounts.json"


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, PRESETS_DIR, TEMPLATES_DIR, LOGS_DIR, FAIL_DIR):
        d.mkdir(parents=True, exist_ok=True)


def mock_command() -> list[str]:
    """Command line that starts the bundled ProTanki mock client."""
    if FROZEN:
        return [str(ROOT / "ProTankiMock.exe")]
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")      # no console window per client
    return [str(pythonw if pythonw.exists() else exe), str(ROOT / "mock" / "mock_protanki.py")]
