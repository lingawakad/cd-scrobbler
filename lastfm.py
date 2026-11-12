"""Last.fm: submit listens via pylast."""

import pylast

from config import get_lastfm_api_key, get_lastfm_api_secret, get_lastfm_session_key

USER_AGENT = "cd-scrobbler/0.3.0 (recent8571@proton.me)"

# Cached network instance — created once and reused.
_network = None
_username = None


def _ensure_network(config: dict):
    """Lazily create and cache a pylast Network instance."""
    global _network, _username
    if _network is not None:
        return _network, _username

    api_key = get_lastfm_api_key(config)
    session_key = get_lastfm_session_key(config)
    api_secret = get_lastfm_api_secret(config)

    if not api_key or not session_key:
        raise LookupError(
            "Last.fm credentials not configured — "
            "add lastfm_api_key to ~/.config/cd-scrobbler/config "
            "and run: cdscrobble lastfm login"
        )

    _network = pylast.LastFMNetwork(
        api_key=api_key,
        api_secret=api_secret,
        session_key=session_key,
    )
    try:
        _username = _network.get_username()
    except Exception:
        # Session may be expired or invalid — reset cache so next call retries fresh
        _network = None
        _username = None
        raise

    return _network, _username


def submit_listen(
    track: dict,
    listened_at: int,
    config: dict,
) -> None:
    """Submit a single listen to Last.fm."""
    net, username = _ensure_network(config)
    scrobbler = net.get_user(username).get_scrobbler()
    scrobbler.scrobble(
        artist=track["artist"],
        title=track["title"],
        timestamp=listened_at,
        album=track.get("album", "") or None,
    )


def submit_playing_now(
    track: dict,
    config: dict,
) -> None:
    """Update Now Playing on Last.fm."""
    net, username = _ensure_network(config)
    scrobbler = net.get_user(username).get_scrobbler()
    scrobbler.update_now_playing(
        artist=track["artist"],
        title=track["title"],
        album=track.get("album", "") or None,
    )


def clear_playing_now(config: dict) -> None:
    """Last.fm doesn't have a clear Now Playing endpoint — no-op."""
    pass


def get_recent_tracks(
    limit: int = 25,
    config: dict | None = None,
) -> list[dict]:
    """Fetch recent scrobbles from Last.fm."""
    if config is None:
        config = {}
    net, username = _ensure_network(config)
    user = net.get_user(username)
    track_info = user.get_recent_tracks(limit=limit)

    results = []
    for t in track_info:
        results.append({
            "artist": str(t.artist),
            "title": str(t.title),
            "album": str(t.album) if t.album else "",
            "timestamp": int(t.timestamp),
        })
    return results
