"""mpv IPC: communicate with mpv via JSON IPC socket."""

import json
import os
import socket
import time
from pathlib import Path


def _get_default_socket() -> str:
    return "/tmp/mpvsocket"


class MpvDisconnected(Exception):
    """mpv IPC socket closed unexpectedly."""


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


def mpv_events(sock_path: str, event_timeout: float = 2.0):
    """Yield mpv event dicts from the IPC socket.

    Connects to mpv, wakes it with a probe, then enters an event stream.
    Yields dicts like {"event": "end-file"},
    {"event": "property-change", "property": "time-pos", "data": ...},
    etc.  When mpv disconnects the generator stops (caller gets
    ``StopIteration``).  On transient connect failures it retries once
    before giving up.
    """
    # Try to establish a persistent connection with mpv.
    s = None
    for _ in range(100):  # ~30s total wait
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(event_timeout)
        try:
            s.connect(sock_path)
            break
        except (FileNotFoundError, ConnectionRefusedError, OSError):
            s.close()
            s = None
            time.sleep(0.3)
            continue

    if s is None:
        raise ConnectionError(f"Cannot connect to mpv at {sock_path}")

    try:
        # Wake mpv so it starts the event stream.
        try:
            s.sendall(b'{"command": ["get_property", "time-pos"]}\n')
            s.sendall(b'{"command": ["observe_property", 1, "time-pos", "double"]}\n')
            # CDDA: also track playlist position (track index) as CDDA may not push time-pos continuously
            s.sendall(b'{"command": ["observe_property", 1, "playlist-pos", "integer"]}\n')
            s.sendall(b'{"command": ["observe_property", 1, "playlist", "json"]}\n')
        except Exception:
            pass
        # Don't drain the socket — initial probe response and early events
        # are consumed by the event loop below (probe response has no
        # "event" key so it's silently skipped).

        s.settimeout(event_timeout)
        decoder = json.JSONDecoder()
        buf = b""
        while True:
            try:
                chunk = s.recv(4096)
                if not chunk:
                    raise MpvDisconnected("socket closed")
                buf += chunk
            except socket.timeout:
                # No new data — nothing to yield, loop back to recv
                continue

            # Decode any complete JSON objects from the buffer,
            # preserving partial objects across recv calls.
            text = buf.decode("utf-8", errors="replace")
            pos = 0
            while pos < len(text):
                # Skip whitespace / newlines between objects.
                while pos < len(text) and text[pos] in (" ", "\n", "\r", "\t"):
                    pos += 1
                if pos >= len(text):
                    break
                try:
                    obj, end = decoder.raw_decode(text, pos)
                    pos = end
                    if isinstance(obj, dict) and obj.get("event"):
                        yield obj
                except json.JSONDecodeError:
                    pos += 1  # skip one byte, try again

            # Keep unconsumed bytes (partial JSON) for the next recv.
            buf = text[pos:].encode("utf-8")

    except MpvDisconnected:
        pass  # mpv died — caller sees no more events
    finally:
        try:
            s.close()
        except Exception:
            pass


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
