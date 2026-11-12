"""mpv IPC: communicate with mpv via JSON IPC socket."""

import json
import os
import socket
import time
from pathlib import Path


def _get_default_socket() -> str:
    return "/tmp/mpvsocket"


def _mpv_request(sock_path: str, command: list, timeout: float = 5) -> dict:
    """Send a JSON IPC request to mpv and return the response."""
    req = {"command": command}
    data = json.dumps(req).encode() + b"\n"

    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(sock_path)
    except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
        raise ConnectionError(f"Cannot connect to mpv at {sock_path}: {e}")

    try:
        s.sendall(data)

        # Read response (mpv sends newline-delimited JSON)
        chunks = []
        while True:
            try:
                chunk = s.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            except socket.timeout:
                break

        response = b"".join(chunks).decode().strip()
        if not response:
            raise ConnectionError("mpv returned empty response")
        # mpv may send multiple JSON objects (events); parse the first one
        resp, _ = json.JSONDecoder().raw_decode(response)
        return resp
    finally:
        s.close()


def mpv_get_property(sock_path: str, prop: str, timeout: float = 5):
    """Get an mpv property (e.g. 'pause', 'time-pos', 'duration')."""
    resp = _mpv_request(sock_path, ["get_property", prop], timeout)
    if resp.get("error") == "success":
        return resp.get("data")
    raise RuntimeError(f"mpv get_property({prop!r}) failed: {resp.get('error')}")


def mpv_command(sock_path: str, *args, timeout: float = 5) -> None:
    """Send a command to mpv (e.g. 'play', 'pause', 'stop', 'seek', 120)."""
    resp = _mpv_request(sock_path, list(args), timeout)
    if resp.get("error") != "success":
        raise RuntimeError(f"mpv command {list(args)} failed: {resp.get('error')}")


def mpv_get_status(sock_path: str) -> str:
    """Return 'Playing', 'Paused', or 'Stopped'.

    If 'eof-reached' is unavailable (older mpv or audio-only),
    this returns 'Playing' and the caller should use a position-based
    fallback to detect playback completion.
    """
    try:
        paused = mpv_get_property(sock_path, "pause")
    except (ConnectionError, RuntimeError):
        raise
    if paused:
        return "Paused"
    # Check if playback ended (may not work for audio-only in some mpv versions)
    try:
        eof_reached = mpv_get_property(sock_path, "eof-reached")
    except (ConnectionError, RuntimeError):
        return "Playing"
    if eof_reached:
        return "Stopped"
    return "Playing"


def mpv_get_position(sock_path: str) -> float:
    """Return current playback position in seconds."""
    return float(mpv_get_property(sock_path, "time-pos") or 0)


def mpv_get_duration(sock_path: str) -> float:
    """Return total duration in seconds."""
    return float(mpv_get_property(sock_path, "duration") or 0)


def wait_for_mpv(sock_path: str, timeout: int = 30, verbose: bool = False) -> bool:
    """Wait until mpv is ready and playing. Returns True if successful."""
    for i in range(timeout):
        time.sleep(1)
        try:
            status = mpv_get_status(sock_path)
            if status == "Playing":
                return True
        except (ConnectionError, FileNotFoundError, OSError, RuntimeError):
            pass
    return False


def is_mpv_ready(sock_path: str) -> bool:
    """Check if mpv is accepting IPC connections."""
    try:
        mpv_get_property(sock_path, "pause")
        return True
    except Exception:
        return False
