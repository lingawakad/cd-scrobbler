"""Last.fm: submit listens via pylast (7.x API)."""

from __future__ import annotations

import pylast

from config import get_lastfm_api_key, get_lastfm_api_secret, get_lastfm_session_key


def _get_network() -> pylast.LastFMNetwork | None:
    """Return a cached pylast.LastFMNetwork, or None if not configured."""
    api_key = get_lastfm_api_key()
    session_key = get_lastfm_session_key()
    if not api_key or not session_key:
        return None
    if (
        getattr(pylast, "_default_network", None) is not None
        and getattr(pylast._default_network, "api_key", None) == api_key
        and getattr(pylast._default_network, "session_key", None) == session_key
    ):
        return pylast._default_network
    net = pylast.LastFMNetwork(api_key=api_key, session_key=session_key)
    pylast._default_network = net
    return net


def submit_listen(
    track: dict,
    listened_at: int,
    config: dict | None = None,
) -> None:
    """Submit a single listen to Last.fm."""
    if config is not None:
        api_key = get_lastfm_api_key(config)
        session_key = get_lastfm_session_key(config)
    else:
        api_key = get_lastfm_api_key()
        session_key = get_lastfm_session_key()
    if not api_key or not session_key:
        raise LookupError("Last.fm credentials not configured")

    try:
        pylast.scrobble(
            artist=track["artist"],
            title=track["title"],
            timestamp=listened_at,
            album=track.get("album", "") or None,
        )
    except (TypeError, AttributeError):
        # Fallback: use LastFMNetwork instance
        net = _get_network()
        if net is None:
            raise LookupError("Last.fm credentials not configured")
        net.scrobble(
            artist=track["artist"],
            title=track["title"],
            timestamp=listened_at,
            album=track.get("album", "") or None,
        )


def submit_playing_now(
    track: dict,
    config: dict | None = None,
) -> None:
    """Update Now Playing on Last.fm."""
    if config is not None:
        api_key = get_lastfm_api_key(config)
        session_key = get_lastfm_session_key(config)
    else:
        api_key = get_lastfm_api_key()
        session_key = get_lastfm_session_key()
    if not api_key or not session_key:
        raise LookupError("Last.fm credentials not configured")

    try:
        pylast.update_now_playing(
            artist=track["artist"],
            title=track["title"],
            album=track.get("album", "") or None,
        )
    except (TypeError, AttributeError):
        # Fallback: use LastFMNetwork instance
        net = _get_network()
        if net is None:
            raise LookupError("Last.fm credentials not configured")
        net.update_now_playing(
            artist=track["artist"],
            title=track["title"],
            album=track.get("album", "") or None,
        )


def clear_playing_now(config: dict | None = None) -> None:
    """Last.fm doesn't have a clear Now Playing endpoint — no-op."""
    pass


def get_recent_tracks(
    limit: int = 25,
    config: dict | None = None,
) -> list[dict]:
    """Fetch recent scrobbles from Last.fm."""
    net = _get_network()
    if net is None:
        raise LookupError("Last.fm credentials not configured")
    try:
        user = pylast.get_authenticated_user()
    except TypeError:
        # Fallback: pylast 7.x might not have get_authenticated_user
        user = net.get_user(name=net.username)
    tracks = user.get_recent_tracks(limit=limit)

    results = []
    for t in tracks:
        results.append({
            "artist": str(t.artist),
            "title": str(t.title),
            "album": str(t.album) if t.album else "",
            "timestamp": int(t.timestamp),
        })
    return results
