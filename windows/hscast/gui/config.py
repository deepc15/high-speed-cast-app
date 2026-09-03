"""Configuration persistence for HSCast GUI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "mode": "mirror",               # "mirror" or "desktop"
    "conn_type": "usb",             # "usb" or "wifi"
    "phone_ip": "",
    "recent_ips": [],
    "serial": "",
    "bitrate": 12_000_000,
    "fps": 60,
    "codec": "h264",
    "hwaccel": True,
    "vsync": False,
    "control": True,
    "cursor": True,
    "max_size": 1920,
    "monitor": 0,
    "scale_filter": "AREA",
    "custom_adb_path": "",
    "record": "",
    "preset": "balanced",
}


def get_config_path() -> Path:
    app_data = os.getenv("APPDATA")
    if app_data:
        base_dir = Path(app_data) / "HSCast"
    else:
        base_dir = Path.home() / ".hscast"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "config.json"


def load_config() -> dict[str, Any]:
    path = get_config_path()
    config = dict(DEFAULT_CONFIG)
    if path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    config.update(saved)
        except Exception:
            pass
    return config


def save_config(updates: dict[str, Any]) -> dict[str, Any]:
    path = get_config_path()
    config = load_config()
    config.update(updates)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except Exception:
        pass
    return config
