"""ListenBrainz: submit listened tracks."""

import socket
import urllib3
from pathlib import Path

import requests


# IPv4-only transport — avoids sporadic IPv6 resolution failures without
# monkey-patching the global socket module.
class _IPv4ConnectionPool(urllib3.HTTPSConnectionPool):
    """HTTPSConnectionPool that forces AF_INET DNS resolution."""
    def _new_conn(self):
        import urllib3.connection
        # Force AF_INET by resolving hostname ourselves
        infos = socket.getaddrinfo(self.host, self.port, socket.AF_INET)
        if infos:
            family, _, _, _, sockaddr = infos[0]
            return urllib3.connection.HTTPSConnection(
                self.host, self.port, timeout=self.timeout,
                source_address=sockaddr, assert_hostname=False,
            )
        raise urllib3.exceptions.MaxRetryError(
            urllib3.HTTPConnectionPool(self.host, self.port),
            f"Cannot resolve {self.host} to IPv4",
        )


class _IPv4HTTPAdapter(requests.adapters.HTTPAdapter):
    """Transport adapter for IPv4-only ListenBrainz API connections."""
    def init_poolmanager(self, *args, **kwargs):
        timeout = kwargs.pop("timeout", 10)
        if isinstance(timeout, int):
            timeout = urllib3.util.Timeout(connect=timeout, read=timeout)
        self.poolmanager = _IPv4ConnectionPool(
            "api.listenbrainz.org",
            maxsize=kwargs.pop("maxsize", 10),
            block=kwargs.pop("block", False),
            timeout=timeout,
        )


# Session-scoped to api.listenbrainz.org with IPv4-only resolution.
_API_SESSION: requests.Session | None = None


def _get_api_session() -> requests.Session:
    """Lazy-initialized session for ListenBrainz API (IPv4 only)."""
    global _API_SESSION
    if _API_SESSION is None:
        _API_SESSION = requests.Session()
        _API_SESSION.headers["User-Agent"] = USER_AGENT
        _API_SESSION.mount("https://api.listenbrainz.org/", _IPv4HTTPAdapter())
    return _API_SESSION


API_URL = "https://api.listenbrainz.org/1/submit-listens"
TOKEN_FILE = Path.home() / ".lbtoken"
USER_AGENT = "cd-scrobbler/0.5.0 (recent8571@proton.me)"


def _get_token(config_token: str | None = None) -> str:
    # Config file token takes precedence
    if config_token:
        return config_token
    try:
        raw = TOKEN_FILE.read_text().strip()
        # Handle LB_USER_TOKEN=<token> format
        if "=" in raw:
            return raw.split("=", 1)[1].strip()
        return raw
    except FileNotFoundError:
        raise LookupError(
            f"ListenBrainz token not found at {TOKEN_FILE} — "
            "get one from https://listenbrainz.org/settings/ "
            "or add listenbrainz_token to ~/.config/cd-scrobbler/config"
        )


def submit_listen(track: dict, listened_at: int, config_token: str | None = None) -> None:
    """Submit a single listen to ListenBrainz.

    track: dict with keys — artist, title, album, artist_mbid,
           release_mbid, recording_mbid, length_ms (optional).
    listened_at: Unix timestamp of when playback started.
    """
    token = _get_token(config_token)

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
    metadata["media_player"] = "cd-scrobbler"
    metadata["submission_client"] = "cd-scrobbler"

    payload = [{
        "listened_at": listened_at,
        "track_metadata": metadata,
    }]

    resp = _get_api_session().post(
        API_URL,
        json={"listen_type": "single", "payload": payload},
        headers={"Authorization": f"Token {token}"},
        timeout=10,
    )
    resp.raise_for_status()


def submit_playing_now(track: dict, config_token: str | None = None) -> str:
    """Submit a playing_now notification. Returns recording_msid."""
    token = _get_token(config_token)

    payload = [{
        "track_metadata": {
            "artist_name": track["artist"],
            "track_name": track["title"],
            "release_name": track.get("album", ""),
        },
    }]

    if track.get("recording_mbid"):
        payload[0]["track_metadata"]["recording_mbid"] = track["recording_mbid"]

    resp = _get_api_session().post(
        API_URL,
        json={"listen_type": "playing_now", "payload": payload},
        headers={"Authorization": f"Token {token}"},
        params={"return_msid": "true"},
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()
    return result.get("recording_msid", "")


def clear_playing_now(config_token: str | None = None) -> None:
    """Clear the playing_now notification by submitting an empty payload."""
    token = _get_token(config_token)

    resp = _get_api_session().post(
        API_URL,
        json={"listen_type": "playing_now", "payload": []},
        headers={"Authorization": f"Token {token}"},
        timeout=10,
    )
    resp.raise_for_status()
