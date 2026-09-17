"""Per-user settings and paths for the maker itself (never inside the repo).

The Venice API key lives here — in the user's config directory with 0600
permissions — and is read from ``VENICE_API_KEY`` first if that is set, so a
key never has to be written into any source or project file.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DEFAULTS = {
    "venice_api_key": "",
    "venice_model": "",            # empty = pick Venice's default_code model
    "build_python": "",            # empty = auto-detect
    "output_dir": "",
    "company": "DECINT",
    "vendor_contact": "",          # shown on the activation screen
    "copyright_holder": "DECINT",
    "last_project": "",
}


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        d = base / "DECINT" / "ExeMaker"
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "DECINT" / "ExeMaker"
    else:
        d = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "decint" / "exe-maker"
    d.mkdir(parents=True, exist_ok=True)
    return d


def keys_dir() -> Path:
    d = config_dir() / "keys"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def settings_path() -> Path:
    return config_dir() / "settings.json"


def load() -> dict:
    data = dict(DEFAULTS)
    p = settings_path()
    if p.is_file():
        try:
            data.update(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return data


def save(data: dict) -> None:
    p = settings_path()
    p.write_text(json.dumps({k: data.get(k, v) for k, v in DEFAULTS.items()}, indent=2),
                 encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def venice_key(data: dict | None = None) -> str:
    """Environment wins over the settings file."""
    env = os.environ.get("VENICE_API_KEY", "").strip()
    if env:
        return env
    return ((data or load()).get("venice_api_key") or "").strip()
