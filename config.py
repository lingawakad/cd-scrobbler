"""Configuration: read/write ~/.config/cd-scrobbler/config."""

import configparser
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "cd-scrobbler"
CONFIG_FILE = CONFIG_DIR / "config"

DEFAULTS = {
    "device": "",          # CD/DVD device (empty = auto-detect)
    "verbose": "false",
    "mpv_socket": "",      # mpv IPC socket path (empty = auto)
    "no_playing_now": "false",
}


def _ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Load config file, returning a dict with defaults for missing keys."""
    _ensure_config_dir()
    config = configparser.ConfigParser()
    config.read(str(CONFIG_FILE))

    result = dict(DEFAULTS)
    if "general" in config:
        for key, value in config["general"].items():
            if key in result:
                result[key] = value
    return result


def save_config(updates: dict) -> None:
    """Update config file with the given key-value pairs."""
    _ensure_config_dir()
    config = configparser.ConfigParser()
    config.read(str(CONFIG_FILE))

    if "general" not in config:
        config["general"] = {}

    for key, value in updates.items():
        if key in DEFAULTS:
            config["general"][key] = str(value)

    with open(CONFIG_FILE, "w") as f:
        config.write(f)


def get_device(config: dict) -> str | None:
    """Return the configured device, or None for auto-detect."""
    dev = config.get("device", "").strip()
    return dev if dev else None


def is_verbose(config: dict) -> bool:
    return config.get("verbose", "false").lower() == "true"


def get_mpv_socket(config: dict) -> str | None:
    """Return the configured mpv IPC socket path, or None for auto."""
    sock = config.get("mpv_socket", "").strip()
    return sock if sock else None


def no_playing_now(config: dict) -> bool:
    return config.get("no_playing_now", "false").lower() == "true"
