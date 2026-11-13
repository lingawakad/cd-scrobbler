"""ListenBrainz: submit listened tracks."""

import socket
from pathlib import Path

import requests


_API_SESSION: requests.Session | None = None
_USER_AGENT = "cd-scrobbler/0.5.0 (recent8571@proton.me)"


def _get_api_session() -> requests.Session:
    """Lazy-initialized session for ListenBrainz API."""
    global _API_SESSION
    if _API_SESSION is None:
        _API_SESSION = requests.Session()
        _API_SESSION.headers["User-Agent"] = _USER_AGENT
    return _API_SESSION


def _post_ipv4(path: str, **kwargs) -> requests.Response:
    """POST to ListenBrainz API, forcing AF_INET resolution for this request only."""
    orig_getaddrinfo = socket.getaddrinfo

    def _filtered(*args, **kw):
        results = orig_getaddrinfo(*args, **kw)
        return [r for r in results if r[0] == socket.AF_INET]

    socket.getaddrinfo = _filtered
    try:
        return _get_api_session().post(path, **kwargs)
    finally:
        socket.getaddrinfo = orig_getaddrinfo


API_URL = "https://api.listenbrainz.org/1/submit-listens"
TOKEN_FILE = Path.home() / ".lbtoken"


def _get_token(config_token: str | None = None) -> str:
    if config_token:
        return config_token
    try:
        raw = TOKEN_FILE.read_text().strip()
        if "=" in raw:
            return raw.split("=", 1)[1].strip()
        return raw
    except FileNotFoundError:
        raise LookupError(
            f"ListenBrainz token not found at {TOKEN_FILE} — "
            "get one from https://listenbrainz.org/settings/ "
            "or add listenbrainz_token to ~/.config/cd-scrobbler/config"
        )


def submit_listen(
    track: dict, listened_at: int, config_token: str | None = None,
) -> None:
    token = _get_token(config_token)
    metadata = {
        "artist_name": track["artist"],
        "track_name": track["title"],
        "release_name": track.get("album", ""),
    }
    for key in ("artist_mbid", "release_mbid", "recording_mbid"):
        if track.get(key):
            metadata[f"{key}"] = track[key]
    if track.get("length_ms"):
        metadata["duration_ms"] = track["length_ms"]
    metadata["media_player"] = "cd-scrobbler"
    metadata["submission_client"] = "cd-scrobbler"

    _post_ipv4(API_URL,
        json={"listen_type": "single", "payload": [{
            "listened_at": listened_at, "track_metadata": metadata,
        }]},
        headers={"Authorization": f"Token {token}"},
        timeout=10,
    ).raise_for_status()


def submit_playing_now(
    track: dict, config_token: str | None = None,
) -> str:
    token = _get_token(config_token)
    meta = {
        "artist_name": track["artist"],
        "track_name": track["title"],
        "release_name": track.get("album", ""),
    }
    if track.get("recording_mbid"):
        meta["recording_mbid"] = track["recording_mbid"]
    resp = _post_ipv4(API_URL,
        json={"listen_type": "playing_now", "payload": [{"track_metadata": meta}]},
        headers={"Authorization": f"Token {token}"},
        params={"return_msid": "true"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("recording_msid", "")


def clear_playing_now(config_token: str | None = None) -> None:
    token = _get_token(config_token)
    _post_ipv4(API_URL,
        json={"listen_type": "playing_now", "payload": []},
        headers={"Authorization": f"Token {token}"},
        timeout=10,
    ).raise_for_status()
