"""Configuration: read/write ~/.config/cd-scrobbler/config."""

import configparser
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "cd-scrobbler"
CONFIG_FILE = CONFIG_DIR / "config"

DEFAULTS = {
    "device": "",
    "verbose": "false",
    "mpv_socket": "",
    "no_playing_now": "false",
    "listenbrainz_token": "",
    "listenbrainz_username": "",
    "lastfm_api_key": "",
    "lastfm_api_secret": "",
    "lastfm_session_key": "",
    "lastfm_username": "",
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


class _Config:
    """Typed, lazily-loaded config wrapper."""

    _default: "_Config | None" = None

    def __init__(self, raw: dict | None = None):
        if raw is None:
            raw = load_config()
        self._raw = dict(DEFAULTS)
        self._raw.update(raw)
        self.device: str | None = self._raw.get("device", "").strip() or None
        self.verbose: bool = self._raw.get("verbose", "false").lower() == "true"
        self.mpv_socket: str | None = self._raw.get("mpv_socket", "").strip() or None
        self.no_playing_now: bool = self._raw.get("no_playing_now", "false").lower() == "true"
        self.listenbrainz_token: str | None = (
            self._raw.get("listenbrainz_token", "").strip() or None
        )
        self.listenbrainz_username: str | None = (
            self._raw.get("listenbrainz_username", "").strip() or None
        )
        self.lastfm_api_key: str | None = (
            self._raw.get("lastfm_api_key", "").strip() or None
        )
        self.lastfm_api_secret: str | None = (
            self._raw.get("lastfm_api_secret", "").strip() or None
        )
        self.lastfm_session_key: str | None = (
            self._raw.get("lastfm_session_key", "").strip() or None
        )
        self.lastfm_username: str | None = (
            self._raw.get("lastfm_username", "").strip() or None
        )

    @property
    def lastfm_enabled(self) -> bool:
        return bool(self.lastfm_api_key and self.lastfm_session_key)

    @property
    def raw(self) -> dict:
        """Return the underlying dict (for save_config, etc.)."""
        return self._raw

    @classmethod
    def default(cls) -> "_Config":
        """Return the module-level default config (loaded once)."""
        if cls._default is None:
            cls._default = cls()
        return cls._default

    # Backwards-compatible helpers (for callers that still pass a plain dict).
    @staticmethod
    def get_listenbrainz_token(cfg) -> str | None:
        return (
            cfg.listenbrainz_token
            if hasattr(cfg, "listenbrainz_token")
            else (cfg.get("listenbrainz_token", "").strip() or None)
        )

    @staticmethod
    def get_listenbrainz_username(cfg) -> str | None:
        return (
            cfg.listenbrainz_username
            if hasattr(cfg, "listenbrainz_username")
            else (cfg.get("listenbrainz_username", "").strip() or None)
        )

    @staticmethod
    def get_lastfm_api_key(cfg) -> str | None:
        return (
            cfg.lastfm_api_key
            if hasattr(cfg, "lastfm_api_key")
            else (cfg.get("lastfm_api_key", "").strip() or None)
        )

    @staticmethod
    def get_lastfm_api_secret(cfg) -> str | None:
        return (
            cfg.lastfm_api_secret
            if hasattr(cfg, "lastfm_api_secret")
            else (cfg.get("lastfm_api_secret", "").strip() or None)
        )

    @staticmethod
    def get_lastfm_session_key(cfg) -> str | None:
        return (
            cfg.lastfm_session_key
            if hasattr(cfg, "lastfm_session_key")
            else (cfg.get("lastfm_session_key", "").strip() or None)
        )

    @staticmethod
    def get_lastfm_username(cfg) -> str | None:
        return (
            cfg.lastfm_username
            if hasattr(cfg, "lastfm_username")
            else (cfg.get("lastfm_username", "").strip() or None)
        )

    @staticmethod
    def is_lastfm_enabled(cfg) -> bool:
        if hasattr(cfg, "lastfm_enabled"):
            return cfg.lastfm_enabled
        ak = cfg.get("lastfm_api_key", "").strip()
        sk = cfg.get("lastfm_session_key", "").strip()
        return bool(ak and sk)


# Backwards-compatible standalone helpers (for callers that don't want _Config).


def _cfg(config: dict | _Config | None) -> _Config:  # noqa: ANN001
    """Normalize config to _Config instance."""
    if isinstance(config, _Config):
        return config
    if isinstance(config, dict):
        return _Config(config)
    return _Config()


def get_device(config: dict | _Config) -> str | None:
    """Return the configured device, or None for auto-detect."""
    return _cfg(config).device


def is_verbose(config: dict | _Config) -> bool:
    return _cfg(config).verbose


def get_mpv_socket(config: dict | _Config) -> str | None:
    return _cfg(config).mpv_socket


def no_playing_now(config: dict | _Config) -> bool:
    return _cfg(config).no_playing_now


def get_listenbrainz_token(config: dict | _Config | None = None) -> str | None:
    if config is None:
        config = load_config()
    return _cfg(config).listenbrainz_token


def get_listenbrainz_username(config: dict | _Config | None = None) -> str | None:
    if config is None:
        config = load_config()
    return _cfg(config).listenbrainz_username


def get_lastfm_api_key(config: dict | _Config | None = None) -> str | None:
    if config is None:
        config = load_config()
    return _cfg(config).lastfm_api_key


def get_lastfm_api_secret(config: dict | _Config | None = None) -> str | None:
    if config is None:
        config = load_config()
    return _cfg(config).lastfm_api_secret


def get_lastfm_session_key(config: dict | _Config | None = None) -> str | None:
    if config is None:
        config = load_config()
    return _cfg(config).lastfm_session_key


def get_lastfm_username(config: dict | _Config | None = None) -> str | None:
    if config is None:
        config = load_config()
    return _cfg(config).lastfm_username


def lastfm_enabled(config: dict | _Config) -> bool:
    return _cfg(config).lastfm_enabled
