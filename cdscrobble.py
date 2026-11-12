"""cdscrobble — Scrobble CD playback to ListenBrainz."""

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from config import (
    load_config, save_config, get_device, is_verbose, get_mpv_socket,
    no_playing_now, get_listenbrainz_token,
    get_lastfm_api_key, get_lastfm_api_secret, get_lastfm_session_key, lastfm_enabled,
)
from mbid import get_disc_info, get_cdtext, DiscReadError
from mpv_ipc import (
    _get_default_socket, wait_for_mpv,
    mpv_get_status, mpv_get_position, mpv_command,
)
from musicbrainz import (
    find_release,
    find_release_by_search,
    search_releases,
    get_release_info,
)
from listenbrainz import submit_listen as lb_submit_listen, submit_playing_now as lb_submit_playing_now, clear_playing_now as lb_clear_playing_now
from lastfm import submit_listen as lf_submit_listen, submit_playing_now as lf_submit_playing_now, clear_playing_now as lf_clear_playing_now
from offline_queue import enqueue, flush_queue, queue_size

USER_AGENT = "cd-scrobbler/0.3.0 (recent8571@proton.me)"


def _print_tracks(info: dict) -> None:
    """Print release artist, title, and track listing."""
    print(f"{info['artist']} — {info['title']}")
    for t in info["tracks"]:
        ms = t["length_ms"]
        m, s = divmod(ms // 1000, 60)
        print(f"  {t['position']}: {t['title']} ({m}:{s:02d})")


def main():
    cfg = load_config()

    parser = argparse.ArgumentParser(
        description="Scrobble CD playback to ListenBrainz",
    )
    sub = parser.add_subparsers(dest="command")

    # lookup: identify CD and print metadata
    p_lookup = sub.add_parser("lookup", help="Identify CD and print metadata")
    p_lookup.add_argument("-d", "--device", default=None,
                          help="CD/DVD device (default: auto-detect)")
    p_lookup.add_argument("-v", "--verbose", action="store_true")

    # play: identify CD, start playback, monitor, scrobble
    p_play = sub.add_parser("play", help="Identify CD, start playback, scrobble")
    p_play.add_argument("-d", "--device", default=None,
                        help="CD/DVD device (default: auto-detect)")
    p_play.add_argument("-v", "--verbose", action="store_true")
    p_play.add_argument("--start", action="store_true",
                        help="Start playback after lookup (mpv)")
    p_play.add_argument("--no-playing-now", action="store_true",
                        help="Skip playing_now notifications")
    p_play.add_argument("--socket", default=None,
                        help="mpv IPC socket path (default: auto)")

    # cache: manually associate a disc ID with a release MBID
    p_cache = sub.add_parser("cache", help="Cache a release MBID for the current disc")
    p_cache.add_argument("-d", "--device", default=None,
                         help="CD/DVD device (default: auto-detect)")
    p_cache.add_argument("-v", "--verbose", action="store_true")
    p_cache.add_argument("release_mbid", help="MusicBrainz release ID")

    # history: show recent scrobbles from configured backends
    p_history = sub.add_parser("history", help="Show recent scrobbles")
    p_history.add_argument("-n", "--count", type=int, default=10,
                           help="Number of recent scrobbles to show (default: 10)")
    p_history.add_argument("-s", "--service", choices=["listenbrainz", "lastfm", "both"],
                           default="both", help="Which backend to query (default: both)")
    p_history.add_argument("-v", "--verbose", action="store_true")

    # queue: show or flush the offline queue
    p_queue = sub.add_parser("queue", help="Show or flush offline queue")
    p_queue.add_argument("--flush", action="store_true",
                         help="Attempt to flush the queue now")
    p_queue.add_argument("-v", "--verbose", action="store_true")

    # control: pause, play, stop mpv playback
    p_control = sub.add_parser("control", help="Control mpv playback (pause/play/stop)")
    p_control.add_argument("action", choices=["pause", "play", "stop"],
                           help="Action to perform")
    p_control.add_argument("--socket", default=None,
                           help="mpv IPC socket path (default: auto)")
    p_control.add_argument("-v", "--verbose", action="store_true")

    # lastfm: manage Last.fm session key
    p_lastfm = sub.add_parser("lastfm", help="Manage Last.fm credentials")
    p_lastfm_sub = p_lastfm.add_subparsers(dest="lastfm_command")

    p_lastfm_login = p_lastfm_sub.add_parser("login",
                                              help="Obtain a Last.fm session key")
    p_lastfm_login.add_argument("-v", "--verbose", action="store_true")

    p_lastfm_set = p_lastfm_sub.add_parser("set-key",
                                           help="Manually set the Last.fm session key")
    p_lastfm_set.add_argument("session_key", help="Last.fm session key")

    args = parser.parse_args()

    # Merge CLI args with config: CLI takes precedence
    verbose = args.verbose or is_verbose(cfg)

    cfg_token = get_listenbrainz_token(cfg)

    # Flush any pending queue entries at startup
    if hasattr(args, 'command') and args.command != "queue":
        try:
            ok, remaining = flush_queue(lambda e: _submit_queued(e, cfg))
            if ok and verbose:
                print(f"Flushed {ok} queued scrobble(s)", file=sys.stderr)
            if remaining and verbose:
                print(f"Queue: {remaining} entry/entries remaining", file=sys.stderr)
        except Exception as e:
            if verbose:
                print(f"Queue flush error: {e}", file=sys.stderr)

    if args.command == "lookup":
        cmd_lookup(args, verbose, cfg)
    elif args.command == "play":
        cmd_play(args, verbose, cfg)
    elif args.command == "cache":
        cmd_cache(args, verbose, cfg)
    elif args.command == "history":
        cmd_history(args, cfg)
    elif args.command == "queue":
        cmd_queue(args, verbose, cfg)
    elif args.command == "control":
        cmd_control(args, cfg)
    elif args.command == "lastfm":
        cmd_lastfm(args, cfg, verbose)
    else:
        parser.print_help()
        sys.exit(1)


def _submit_queued(entry: dict, config: dict) -> None:
    """Submit a queued scrobble entry to the appropriate backend."""
    backend = entry.get("backend", "listenbrainz")
    track = entry["track"]
    listened_at = entry.get("listened_at")

    if entry.get("listen_type") == "playing_now":
        if backend == "lastfm":
            lf_submit_playing_now(track, config)
        else:
            lb_submit_playing_now(track, config.get("listenbrainz_token"))
        return

    if listened_at is None:
        raise ValueError("Queue entry missing 'listened_at'")

    if backend == "lastfm":
        lf_submit_listen(track, listened_at, config)
    else:
        lb_submit_listen(track, listened_at, config.get("listenbrainz_token"))


def cmd_lookup(args, verbose, cfg):
    """Identify CD and print metadata."""
    from mbid import find_cd_drive
    from musicbrainz import _load_cache, _get_cached, _set_cached

    device = args.device or get_device(cfg) or find_cd_drive()
    if verbose:
        print(f"Device: {device}", file=sys.stderr)

    disc_id = None
    # Try disc ID lookup
    try:
        disc_id, toc = get_disc_info(device)
        if verbose:
            print(f"Disc ID: {disc_id}", file=sys.stderr)

        # Check cache for disc ID -> release MBID
        cache = _load_cache()
        disc_key = f"disc:{disc_id}"
        cached_mbid = _get_cached(disc_key, cache)
        if cached_mbid:
            if verbose:
                print("Cache hit (disc ID)", file=sys.stderr)
            info = get_release_info(cached_mbid)
            _print_tracks(info)
            return

        release = find_release(disc_id, toc=toc)
        if verbose:
            print(f"Match (disc ID): {release['artist']} — {release['title']}",
                  file=sys.stderr)
    except DiscReadError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        if verbose:
            print(f"Disc ID lookup failed: {e}", file=sys.stderr)
            print("Falling back to CD-Text search...", file=sys.stderr)
        release = _lookup_by_cdtext(device, verbose)
        if release is None:
            sys.exit(1)

        # Cache disc ID -> release MBID
        cache = _load_cache()
        disc_key = f"disc:{disc_id}"
        _set_cached(disc_key, release["mbid"], cache)

    # Get track info
    try:
        info = get_release_info(release["mbid"])
        _print_tracks(info)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_play(args, verbose, cfg, cfg_token=None):
    """Identify CD, start playback via mpv, scrobble tracks."""
    from mbid import DiscReadError, find_cd_drive
    from musicbrainz import _load_cache, _get_cached, _set_cached

    device = args.device or get_device(cfg) or find_cd_drive()
    if verbose:
        print(f"Device: {device}", file=sys.stderr)

    # Step 1: Read disc ID (fast)
    try:
        disc_id, toc = get_disc_info(device)
        if verbose:
            print(f"Disc ID: {disc_id}", file=sys.stderr)
    except DiscReadError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Step 2: Check cache for disc ID -> release MBID
    cache = _load_cache()
    disc_key = f"disc:{disc_id}"
    cached_mbid = _get_cached(disc_key, cache)
    if cached_mbid:
        if verbose:
            print("Cache hit (disc ID)", file=sys.stderr)
        try:
            info = get_release_info(cached_mbid)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        if verbose:
            print(f"{info['artist']} — {info['title']}", file=sys.stderr)
            print(f"Tracks: {len(info['tracks'])}", file=sys.stderr)
    else:
        # Step 3: Lookup CD (API calls)
        try:
            release = find_release(disc_id, toc=toc)
        except Exception as e:
            if verbose:
                print(f"Disc ID lookup failed: {e}", file=sys.stderr)
            release = _lookup_by_cdtext(device, verbose)
            if release is None:
                sys.exit(1)

        # Cache disc ID -> release MBID
        _set_cached(disc_key, release["mbid"], cache)

        # Get track info
        try:
            info = get_release_info(release["mbid"])
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        if verbose:
            print(f"{info['artist']} — {info['title']}", file=sys.stderr)
            print(f"Tracks: {len(info['tracks'])}", file=sys.stderr)

    # Determine socket path
    sock_path = args.socket or get_mpv_socket(cfg) or _get_default_socket()
    if verbose:
        print(f"mpv socket: {sock_path}", file=sys.stderr)

    no_playing = args.no_playing_now or no_playing_now(cfg)

    # Start playback if requested
    if args.start:
        # Ensure parent directory exists for the IPC socket
        Path(sock_path).parent.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            ["mpv", "--demuxer-max-bytes=20MiB",
             "--input-ipc-server=" + sock_path,
             "--ao=pipewire", "--gapless-audio", f"cdda://{device}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if verbose:
            print("Playback started", file=sys.stderr)
        # Wait for mpv to be ready and playing
        if not wait_for_mpv(sock_path, verbose=verbose):
            print("Warning: mpv did not start within 30s", file=sys.stderr)
        # Daemonize: fork so the monitor runs in the background,
        # releasing the prompt immediately.
        _daemonize_monitor(info, sock_path, verbose, no_playing, cfg)
        return


def _monitor_and_scrobble(info: dict, sock_path: str, verbose: bool, no_playing: bool, cfg: dict):
    """Monitor mpv via IPC socket and scrobble tracks to configured backends."""
    tracks_by_pos = {int(t["position"]): t for t in info["tracks"]}
    # Calculate track offsets (cumulative) in seconds
    track_offsets = []
    cumulative = 0
    for t in info["tracks"]:
        track_offsets.append(cumulative)
        cumulative += t.get("length_ms", 0) / 1000  # ms -> seconds
    last_position = None
    start_time = None          # wall-clock time when current track segment started
    scrobbled = set()          # positions already scrobbled
    mpv_connected = False      # have we ever successfully reached mpv?
    was_paused = False         # track pause state to avoid spamming messages
    track_start = None         # wall-clock time when current track first started
    accumulated_play = 0.0     # total playtime for current track across pauses
    lb_enabled = bool(get_listenbrainz_token(cfg))
    lf_enabled = lastfm_enabled(cfg)

    def _scrobble_track(position):
        """Scrobble a track if played long enough (to both enabled backends)."""
        if position not in scrobbled and start_time and track_start:
            played = (time.time() - start_time) + accumulated_play
            track = tracks_by_pos.get(position, {})
            track_len = track.get("length_ms", 0) / 1000 if track.get("length_ms") else 0
            min_played = min(4 * 60, track_len / 2) if track_len > 0 else 4 * 60
            if played > min_played:
                track.setdefault("album", info["title"])
                track.setdefault("artist", info["artist"])
                track.setdefault("release_mbid", info.get("release_mbid", ""))
                ts = int(track_start)
                if lb_enabled:
                    try:
                        lb_submit_listen(track, ts, cfg.get("listenbrainz_token"))
                        if verbose:
                            print(f"Scrobbled (LB): {track.get('title', position)}",
                                  file=sys.stderr)
                    except Exception as e:
                        enqueue({
                            "backend": "listenbrainz",
                            "listen_type": "single",
                            "track": dict(track),
                            "listened_at": ts,
                        })
                        if verbose:
                            print(f"LB scrobble failed, queued: {e}", file=sys.stderr)
                if lf_enabled:
                    try:
                        lf_submit_listen(track, ts, cfg)
                        if verbose:
                            print(f"Scrobbled (LF): {track.get('title', position)}",
                                  file=sys.stderr)
                    except Exception as e:
                        enqueue({
                            "backend": "lastfm",
                            "listen_type": "single",
                            "track": dict(track),
                            "listened_at": ts,
                        })
                        if verbose:
                            print(f"LF scrobble failed, queued: {e}", file=sys.stderr)
                scrobbled.add(position)

    total_duration = track_offsets[-1] if track_offsets else 0

    def _current_track(position_s):
        """Map position (seconds) to track number."""
        if position_s >= total_duration:
            return len(track_offsets)  # at or past end = last track
        for i in range(len(track_offsets) - 1):
            if track_offsets[i] <= position_s < track_offsets[i + 1]:
                return i + 1  # track positions are 1-indexed
        return 1  # position 0 or before first track boundary

    try:
        while True:
            try:
                status = mpv_get_status(sock_path)
                pos_s = mpv_get_position(sock_path)
                mpv_connected = True

                # Detect playback stopped — scrobble current track then exit
                if status == "Stopped" and last_position is not None:
                    _scrobble_track(last_position)
                    if not no_playing:
                        if lb_enabled:
                            try:
                                lb_clear_playing_now(cfg.get("listenbrainz_token"))
                            except Exception:
                                pass
                        if lf_enabled:
                            try:
                                lf_clear_playing_now(cfg)
                            except Exception:
                                pass
                    last_position = None
                    start_time = None
                    break

                # Fallback: mpv may not report "Stopped" for CD/audio-only
                # (eof-reached may not be set). Detect by position.
                if (
                    last_position is not None
                    and total_duration > 0
                    and pos_s >= total_duration
                ):
                    _scrobble_track(last_position)
                    if not no_playing:
                        if lb_enabled:
                            try:
                                lb_clear_playing_now(cfg.get("listenbrainz_token"))
                            except Exception:
                                pass
                        if lf_enabled:
                            try:
                                lf_clear_playing_now(cfg)
                            except Exception:
                                pass
                    last_position = None
                    start_time = None
                    break

                # Detect pause — scrobble current track, stay alive for resume
                if status == "Paused" and last_position is not None:
                    _scrobble_track(last_position)
                    if start_time is not None:
                        accumulated_play += time.time() - start_time
                    if not was_paused:
                        was_paused = True
                        if verbose:
                            print("Paused — waiting for resume", file=sys.stderr)
                    time.sleep(1)
                    continue

                # mpv reports total duration before playback starts; ignore until position drops
                if pos_s > 0 and pos_s >= total_duration * 0.99 and last_position is None:
                    time.sleep(1)
                    continue

                current_track = _current_track(pos_s) if pos_s > 0 else None

                if verbose and current_track:
                    print(f"Track {current_track}", file=sys.stderr)

                # Detect resume from pause
                if status == "Playing" and last_position is not None and was_paused:
                    was_paused = False
                    start_time = time.time()
                    if verbose:
                        print("Resumed", file=sys.stderr)

                # Detect track change
                if current_track is not None and current_track != last_position:
                    # Scrobble previous track
                    if last_position is not None:
                        _scrobble_track(last_position)

                    # New track started — record when this track began
                    start_time = time.time()
                    track_start = time.time()
                    accumulated_play = 0.0
                    was_paused = False
                    last_position = current_track

                    # Submit playing_now to enabled backends
                    if not no_playing:
                        track = tracks_by_pos.get(current_track, {})
                        track.setdefault("album", info["title"])
                        track.setdefault("artist", info["artist"])
                        track.setdefault("release_mbid", info.get("release_mbid", ""))
                        if lb_enabled:
                            try:
                                lb_submit_playing_now(
                                    track, cfg.get("listenbrainz_token"))
                                if verbose:
                                    print(f"Now (LB): {track.get('title', current_track)}",
                                          file=sys.stderr)
                            except Exception as e:
                                if verbose:
                                    print(f"LB playing_now failed: {e}", file=sys.stderr)
                        if lf_enabled:
                            try:
                                lf_submit_playing_now(track, cfg)
                                if verbose:
                                    print(f"Now (LF): {track.get('title', current_track)}",
                                          file=sys.stderr)
                            except Exception as e:
                                if verbose:
                                    print(f"LF playing_now failed: {e}", file=sys.stderr)

            except ConnectionError:
                # mpv not connected yet — wait; but if it was connected, it exited
                if mpv_connected:
                    if last_position is not None:
                        _scrobble_track(last_position)
                    if verbose:
                        print("mpv disconnected", file=sys.stderr)
                    break
                pass
            except Exception as e:
                if verbose:
                    print(f"Unexpected error in monitor loop: {e}", file=sys.stderr)

            time.sleep(1)

    except KeyboardInterrupt:
        if last_position is not None:
            _scrobble_track(last_position)
        if not no_playing:
            if lb_enabled:
                try:
                    lb_clear_playing_now(cfg.get("listenbrainz_token"))
                except Exception:
                    pass
            if lf_enabled:
                try:
                    lf_clear_playing_now(cfg)
                except Exception:
                    pass
        if verbose:
            print("\nInterrupted", file=sys.stderr)


def _daemonize_monitor(info, sock_path, verbose, no_playing, cfg):
    """Fork a child process to run the monitor, releasing the parent immediately."""
    try:
        pid = os.fork()
    except OSError as e:
        print(f"Error: fork failed: {e}", file=sys.stderr)
        sys.exit(1)

    if pid > 0:
        # Parent: return control to the shell immediately
        if verbose:
            print("Monitor daemon started (PID {})".format(pid), file=sys.stderr)
        return

    # Child: detach from terminal and run monitor
    os.setsid()
    # Redirect stdio to devnull so child doesn't hold onto tty
    sys.stdin.close()
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)

    # Re-run monitor in the background child
    _monitor_and_scrobble(info, sock_path, verbose, no_playing, cfg)
    os._exit(0)


def cmd_cache(args, verbose, cfg):
    """Read disc ID and cache it against a user-provided release MBID."""
    from mbid import DiscReadError, find_cd_drive
    from musicbrainz import _load_cache, _get_cached, _set_cached

    # Accept full MusicBrainz URL or raw MBID
    mbid = args.release_mbid
    if mbid.startswith("http"):
        mbid = mbid.rstrip("/").split("/")[-1]
    if not re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", mbid):
        print(f"Error: invalid release MBID: {args.release_mbid}", file=sys.stderr)
        sys.exit(1)

    device = args.device or get_device(cfg) or find_cd_drive()
    if verbose:
        print(f"Device: {device}", file=sys.stderr)

    try:
        disc_id, _ = get_disc_info(device)
    except DiscReadError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    cache = _load_cache()
    disc_key = f"disc:{disc_id}"

    existing = _get_cached(disc_key, cache)
    if existing:
        print(f"Already cached: {disc_key} -> {existing}")
        if existing != mbid:
            print(f"Overwriting with {mbid}...", file=sys.stderr)

    _set_cached(disc_key, mbid, cache)

    # Verify by fetching release info
    try:
        info = get_release_info(mbid)
        print(f"Cached: {disc_id} -> {info['artist']} — {info['title']}")
    except Exception as e:
        print(f"Cached (verify failed: {e})", file=sys.stderr)


def cmd_history(args, cfg):
    """Show recent scrobbles from configured backends."""
    service = args.service.lower() if hasattr(args, 'service') else "both"

    if service in ("both", "listenbrainz"):
        try:
            token = get_listenbrainz_token(cfg) or _get_lb_token_from_file()
        except LookupError as e:
            print(f"ListenBrainz: {e}", file=sys.stderr)
        else:
            _print_lb_history(token, args.count)

    if service in ("both", "lastfm"):
        if lastfm_enabled(cfg):
            try:
                import lastfm as lastfm_mod
                tracks = lastfm_mod.get_recent_tracks(limit=args.count)
            except Exception as e:
                print(f"Last.fm: {e}", file=sys.stderr)
            else:
                _print_lf_history(tracks)
        else:
            print("Last.fm: not configured (need api_key + session_key in config)",
                  file=sys.stderr)


def _get_lb_token_from_file():
    """Fallback: read ListenBrainz token from ~/.lbtoken."""
    from listenbrainz import TOKEN_FILE
    try:
        raw = TOKEN_FILE.read_text().strip()
        if "=" in raw:
            return raw.split("=", 1)[1].strip()
        return raw
    except FileNotFoundError:
        raise LookupError(
            f"ListenBrainz token not found at {TOKEN_FILE} — "
            "put it in ~/.config/cd-scrobbler/config as listenbrainz_token="
        )


def _print_lb_history(token, count):
    """Fetch and display ListenBrainz history."""
    import requests

    try:
        user_resp = requests.get(
            "https://api.listenbrainz.org/1/me/username",
            headers={"Authorization": f"Token {token}"},
            timeout=10,
        )
        user_resp.raise_for_status()
        username = user_resp.json()["user_name"]
    except Exception as e:
        print(f"ListenBrainz: error fetching username: {e}", file=sys.stderr)
        return

    try:
        resp = requests.get(
            f"https://api.listenbrainz.org/1/user/{username}/listens",
            params={"limit": count},
            headers={"Authorization": f"Token {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"ListenBrainz: error fetching history: {e}", file=sys.stderr)
        return

    listens = data.get("payload", [])
    if not listens:
        print("ListenBrainz: No recent scrobbles found.")
        return

    print("ListenBrainz:")
    for listen in listens:
        meta = listen.get("track_metadata", {})
        artist = meta.get("artist_name", "Unknown")
        title = meta.get("track_name", "Unknown")
        album = meta.get("release_name", "")
        ts = listen.get("listened_at", 0)
        dt = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
        album_str = f" ({album})" if album else ""
        print(f"  {dt}  {artist} — {title}{album_str}")


def _print_lf_history(tracks):
    """Display Last.fm history entries."""
    print("Last.fm:")
    for t in tracks:
        artist = t["artist"]
        title = t["title"]
        album = t.get("album", "")
        ts = t.get("timestamp", 0)
        dt = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
        album_str = f" ({album})" if album else ""
        print(f"  {dt}  {artist} — {title}{album_str}")


def cmd_queue(args, verbose, cfg):
    """Show or flush the offline queue."""
    from offline_queue import load_queue
    queue = load_queue()
    print(f"Queue: {len(queue)} pending entry/entries")

    if verbose and queue:
        for i, entry in enumerate(queue):
            track = entry.get("track", {})
            ts = entry.get("listened_at", 0)
            dt = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "???"
            retries = entry.get("retries", 0)
            title = track.get("title", "???")
            artist = track.get("artist", "")
            print(f"  [{i+1}] {dt}  {artist} — {title}  (retries: {retries})")

    if args.flush:
        ok, remaining = flush_queue(lambda e: _submit_queued(e, cfg))
        if ok:
            print(f"Flushed: {ok} scrobble(s) submitted")
        if remaining:
            print(f"Remaining: {remaining} (will retry next run)")
        else:
            print("Queue is now empty")


def cmd_control(args, cfg):
    """Control mpv playback via IPC."""
    sock_path = args.socket or get_mpv_socket(cfg) or _get_default_socket()

    try:
        if args.action == "pause":
            mpv_command(sock_path, "set_property", "pause", "yes")
            print("Paused")
        elif args.action == "play":
            mpv_command(sock_path, "set_property", "pause", "no")
            print("Playing")
        elif args.action == "stop":
            mpv_command(sock_path, "stop")
            print("Stopped")
    except ConnectionError:
        print(f"Error: cannot connect to mpv at {sock_path}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def _lookup_by_cdtext(device, verbose=False):
    """Fallback: search by CD-Text."""
    cdtext = get_cdtext(device)
    if not cdtext["performer"] or not cdtext["title"]:
        print("Error: No CD-Text found on disc", file=sys.stderr)
        return None

    query = f"{cdtext['performer']} {cdtext['title']}"
    if verbose:
        print(f"CD-Text: {query}", file=sys.stderr)

    # Check cache for CD-Text -> release MBID
    from musicbrainz import _load_cache, _get_cached, _set_cached
    cache = _load_cache()
    ct_key = f"cdtext:{query}:{len(cdtext['tracks'])}"
    cached_mbid = _get_cached(ct_key, cache)
    if cached_mbid:
        if verbose:
            print("Cache hit (CD-Text)", file=sys.stderr)
        info = get_release_info(cached_mbid)
        return {"mbid": cached_mbid, "title": info["title"], "artist": info["artist"]}

    results = search_releases(query)
    if not results:
        print(f"Error: No MusicBrainz match for '{query}'", file=sys.stderr)
        return None

    try:
        release = find_release_by_search(results, cdtext["tracks"])
    except LookupError as e:
        print(f"Error: {e}", file=sys.stderr)
        return None

    # Cache CD-Text -> release MBID
    _set_cached(ct_key, release["mbid"], cache)

    if verbose:
        print(f"Match (CD-Text): {release['artist']} — {release['title']}",
              file=sys.stderr)
    return {"mbid": release["mbid"], **release}


def cmd_lastfm(args, cfg, verbose=False):
    """Manage Last.fm credentials."""
    if not hasattr(args, 'lastfm_command') or not args.lastfm_command:
        print("Usage: cdscrobble lastfm <command>")
        print("  login    — Authenticate via mobile auth (username + password)")
        print("  set-key  — Manually set a session key")
        sys.exit(1)

    cmd = args.lastfm_command

    if cmd == "login":
        _lastfm_mobile_login(cfg, verbose)
    elif cmd == "set-key":
        _lastfm_set_key(args.session_key, cfg)


def _lastfm_mobile_login(cfg, verbose=False):
    """Authenticate via pylast mobile auth flow. No browser needed."""
    try:
        import pylast
    except ImportError:
        print("Error: pylast not installed. Run: pip install pylast",
              file=sys.stderr)
        sys.exit(1)

    api_key = get_lastfm_api_key(cfg)
    api_secret = get_lastfm_api_secret(cfg)
    if not api_key:
        print("Error: lastfm_api_key not set in config.", file=sys.stderr)
        print("Add it to ~/.config/cd-scrobbler/config:\n", file=sys.stderr)
        print("  [general]\n", file=sys.stderr)
        print("  lastfm_api_key = YOUR_API_KEY\n", file=sys.stderr)
        sys.exit(1)

    if not api_secret:
        print("Error: lastfm_api_secret not set in config.", file=sys.stderr)
        print("Add it to ~/.config/cd-scrobbler/config:\n", file=sys.stderr)
        print("  lastfm_api_secret = YOUR_SECRET\n", file=sys.stderr)
        sys.exit(1)

    username = input("Last.fm username: ").strip()
    if not username:
        print("No username provided.", file=sys.stderr)
        sys.exit(1)

    password = input("Password: ").strip()
    if not password:
        print("No password provided.", file=sys.stderr)
        sys.exit(1)

    try:
        network = pylast.LastFMNetwork(
            api_key=api_key,
            api_secret=api_secret,
            username=username,
            password_hash=pylast.md5(password),
        )
        session_key = network.session_key
        print(f"Authenticated as {username}")
        print("Session key accepted. Last.fm scrobbling is active.")
        save_config({"lastfm_session_key": session_key})
    except pylast.WSError as e:
        print(f"Error: server response error ({e})", file=sys.stderr)
        sys.exit(1)
    except pylast.PyLastError as e:
        print(f"Error: authentication failed ({e})", file=sys.stderr)
        print("Check your username and password.", file=sys.stderr)
        sys.exit(1)


def _lastfm_set_key(session_key: str, cfg: dict):
    """Save a Last.fm session key to config."""
    save_config({"lastfm_session_key": session_key.strip()})
    # Verify it works
    api_key = get_lastfm_api_key(cfg)
    if not api_key:
        print("Warning: no API key configured. Scrobbles will fail.",
              file=sys.stderr)
        return
    try:
        import pylast
    except ImportError:
        print("Error: pylast not installed. Run: pip install pylast",
              file=sys.stderr)
        return
    try:
        net = pylast.Network(
            api_key=api_key,
            session_key=session_key.strip(),
            user_agent=USER_AGENT,
        )
        username = net.get_username()
        print(f"Session key accepted. User: {username}")
        print("Last.fm scrobbling is now active.")
    except pylast.LastFMError as e:
        print(f"Error: invalid session key ({e})", file=sys.stderr)
        print("Run 'cdscrobble lastfm login' to get a new key.",
              file=sys.stderr)


if __name__ == "__main__":
    main()
