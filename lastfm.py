"""Last.fm: submit listens via pylast."""

import pylast

from config import get_lastfm_api_key, get_lastfm_session_key

USER_AGENT = "cd-scrobbler/0.3.0 (recent8571@proton.me)"


def _get_network(config: dict):
    """Return a pylast Network instance, or raise if credentials are missing."""
    api_key = get_lastfm_api_key(config)
    session_key = get_lastfm_session_key(config)
    if not api_key or not session_key:
        raise LookupError(
            "Last.fm credentials not configured — "
            "add lastfm_api_key to ~/.config/cd-scrobbler/config "
            "and run: cdscrobble lastfm login"
        )
    return pylast.Network(
        api_key=api_key,
        session_key=session_key,
        user_agent=USER_AGENT,
    )


def _get_scrobbler(config: dict):
    """Return a Last.fm Scrobbler for the configured user."""
    net = _get_network(config)
    username = net.get_username()
    return net.get_user(username).get_scrobbler()


def submit_listen(
    track: dict,
    listened_at: int,
    config: dict,
) -> None:
    """Submit a single listen to Last.fm.

    track: dict with keys — artist, title, album (optional).
    listened_at: Unix timestamp of when playback started.
    config: config dict with lastfm_api_key and lastfm_session_key.
    """
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
) -> str:
    """Update Now Playing on Last.fm. Returns recording_mbid if available."""
    scrobbler = _get_scrobbler(config)
    scrobbler.update_now_playing(
        artist=track["artist"],
        title=track["title"],
        album=track.get("album", "") or None,
    )
    return track.get("recording_mbid", "")


def clear_playing_now(config: dict) -> None:
    """Last.fm doesn't have a clear Now Playing endpoint — no-op."""
    pass


def get_recent_tracks(
    limit: int = 25,
    config: dict | None = None,
) -> list[dict]:
    """Fetch recent scrobbles from Last.fm.

    Returns list of dicts: artist, title, album, timestamp.
    """
    if config is None:
        config = {}
    net = _get_network(config)
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
