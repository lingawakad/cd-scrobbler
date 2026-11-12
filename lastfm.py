"""Last.fm: submit listens via pylast."""

import pylast

from config import get_lastfm_api_key, get_lastfm_api_secret, get_lastfm_session_key

USER_AGENT = "cd-scrobbler/0.3.0 (recent8571@proton.me)"

# Cached network instance — created once and reused.
_cached_network = None
_cached_username = None


def _get_scrobbler(config: dict):
    """Return a Last.fm Scrobbler for the configured user.

    Caches the Network instance so we don't re-authenticate on every call.
    Handles both old pylast (get_scrobbler method) and new pylast (scrobbler attr).
    """
    global _cached_network, _cached_username

    if _cached_network is not None:
        user = _cached_network.get_user(_cached_username)
        # pylast 2.x+: scrobbler is a property, not a method
        if hasattr(user, "get_scrobbler"):
            return user.get_scrobbler()
        return user.scrobbler

    api_key = get_lastfm_api_key(config)
    api_secret = get_lastfm_api_secret(config)
    session_key = get_lastfm_session_key(config)

    if not api_key or not session_key:
        raise LookupError(
            "Last.fm credentials not configured — "
            "add lastfm_api_key to ~/.config/cd-scrobbler/config "
            "and run: cdscrobble lastfm login"
        )

    _cached_network = pylast.Network(
        api_key=api_key,
        session_key=session_key,
    )
    _cached_username = _cached_network.get_username()
    user = _cached_network.get_user(_cached_username)
    if hasattr(user, "get_scrobbler"):
        return user.get_scrobbler()
    return user.scrobbler


def submit_listen(
    track: dict,
    listened_at: int,
    config: dict,
) -> None:
    """Submit a single listen to Last.fm."""
    scrobbler = _get_scrobbler(config)
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
    scrobbler = _get_scrobbler(config)
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
    net = pylast.Network(
        api_key=get_lastfm_api_key(config),
        session_key=get_lastfm_session_key(config),
    )
    username = net.get_username()
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
