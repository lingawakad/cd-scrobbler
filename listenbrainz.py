"""ListenBrainz: submit listened tracks."""

import socket
from pathlib import Path

import requests

# Force IPv4 to avoid sporadic IPv6 connectivity issues.
_original_getaddrinfo = socket.getaddrinfo
def _ipv4_only_getaddrinfo(*args, **kwargs):
    return [r for r in _original_getaddrinfo(*args, **kwargs)
            if r[0] == socket.AF_INET]
socket.getaddrinfo = _ipv4_only_getaddrinfo

API_URL = "https://api.listenbrainz.org/1/submit-listens"
TOKEN_FILE = Path.home() / ".lbtoken"
USER_AGENT = "cd-scrobbler/0.1.0 (recent8571@proton.me)"


def _get_token() -> str:
    try:
        raw = TOKEN_FILE.read_text().strip()
        # Handle LB_USER_TOKEN=<token> format
        if "=" in raw:
            return raw.split("=", 1)[1].strip()
        return raw
    except FileNotFoundError:
        raise LookupError(
            f"ListenBrainz token not found at {TOKEN_FILE} — "
            "get one from https://listenbrainz.org/settings/"
        )


def submit_listen(track: dict, listened_at: int) -> None:
    """Submit a single listen to ListenBrainz.

    track: dict with keys — artist, title, album, artist_mbid,
           release_mbid, recording_mbid, length_ms (optional).
    listened_at: Unix timestamp of when playback started.
    """
    token = _get_token()

    metadata = {
        "artist_name": track["artist"],
        "track_name": track["title"],
        "release_name": track.get("album", ""),
    }
    if track.get("artist_mbid"):
        metadata["artist_mbid"] = track["artist_mbid"]
    if track.get("release_mbid"):
        metadata["release_mbid"] = track["release_mbid"]
    if track.get("recording_mbid"):
        metadata["recording_mbid"] = track["recording_mbid"]
    if track.get("length_ms"):
        metadata["duration_ms"] = track["length_ms"]
    metadata["media_player"] = "Strawberry"
    metadata["submission_client"] = "cd-scrobbler"

    payload = [{
        "listened_at": listened_at,
        "track_metadata": metadata,
    }]

    resp = requests.post(
        API_URL,
        json={"listen_type": "single", "payload": payload},
        headers={"Authorization": f"Token {token}", "User-Agent": USER_AGENT},
        timeout=10,
    )
    resp.raise_for_status()


def submit_playing_now(track: dict) -> str:
    """Submit a playing_now notification. Returns recording_msid."""
    token = _get_token()

    payload = [{
        "track_metadata": {
            "artist_name": track["artist"],
            "track_name": track["title"],
            "release_name": track.get("album", ""),
        },
    }]

    if track.get("recording_mbid"):
        payload[0]["track_metadata"]["recording_mbid"] = track["recording_mbid"]

    resp = requests.post(
        API_URL,
        json={"listen_type": "playing_now", "payload": payload},
        headers={"Authorization": f"Token {token}", "User-Agent": USER_AGENT},
        params={"return_msid": "true"},
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()
    return result.get("recording_msid", "")
