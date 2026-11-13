"""Last.fm: submit listens via pylast (7.x API)."""

from __future__ import annotations

import pylast
import requests

from config import _Config


def _get_network(config: _Config | dict | None) -> pylast.LastFMNetwork:
    """Return a cached pylast.LastFMNetwork instance. Raises if not configured."""
    cfg = _Config.default() if config is None else config
    if isinstance(cfg, dict):
        cfg = _Config(cfg)

    if not cfg.lastfm_api_key or not cfg.lastfm_session_key:
        raise LookupError("Last.fm credentials not configured")

    cache: dict[tuple, pylast.LastFMNetwork] = getattr(_get_network, "_cache", {})
    key = (cfg.lastfm_api_key, cfg.lastfm_api_secret, cfg.lastfm_session_key)
    if key in cache:
        return cache[key]

    net = pylast.LastFMNetwork(
        api_key=cfg.lastfm_api_key,
        api_secret=cfg.lastfm_api_secret,
        session_key=cfg.lastfm_session_key,
    )
    cache[key] = net
    return net


def submit_listen(
    track: dict,
    listened_at: int,
    config: _Config | dict | None = None,
) -> None:
    """Submit a single listen to Last.fm."""
    net = _get_network(config)
    net.scrobble(
        artist=track["artist"],
        title=track["title"],
        timestamp=listened_at,
        album=track.get("album", "") or None,
    )


def submit_playing_now(
    track: dict,
    config: _Config | dict | None = None,
) -> None:
    """Update Now Playing on Last.fm."""
    net = _get_network(config)
    net.update_now_playing(
        artist=track["artist"],
        title=track["title"],
        album=track.get("album", "") or None,
    )


def clear_playing_now(config: _Config | dict | None = None) -> None:
    """Last.fm doesn't have a clear Now Playing endpoint — no-op."""
    del config  # no-op regardless


def get_recent_tracks(
    limit: int = 25,
    config: _Config | dict | None = None,
) -> list[dict]:
    """Fetch recent scrobbles from Last.fm via the JSON API."""
    cfg = _Config.default() if config is None else config
    if isinstance(cfg, dict):
        cfg = _Config(cfg)

    if (not cfg.lastfm_username or not cfg.lastfm_api_key
            or not cfg.lastfm_session_key):
        raise LookupError(
            "Last.fm credentials not configured "
            "(need username, api_key + session_key)"
        )

    params = {
        "method": "user.getrecenttracks",
        "user": cfg.lastfm_username,
        "api_key": cfg.lastfm_api_key,
        "sk": cfg.lastfm_session_key,
        "limit": limit,
        "format": "json",
    }
    resp = requests.get(
        "https://ws.audioscrobbler.com/2.0/",
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    tracks = data.get("recenttracks", {}).get("track", [])
    results = []
    for t in tracks:
        album = t.get("album", "")
        results.append({
            "artist": t["artist"]["#text"],
            "title": t["name"],
            "album": album.get("#text", "") if isinstance(album, dict) else album,
            "timestamp": int(t.get("@date", {}).get("uts", 0)),
        })
    return results
