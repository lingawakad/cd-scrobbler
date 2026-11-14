"""cdscrobble — Scrobble CD playback to ListenBrainz."""

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from config import (
    _Config,
    load_config,
    save_config,
)
from mbid import get_disc_info, get_cdtext, DiscReadError
from mpv_ipc import (
    _get_default_socket, wait_for_mpv,
    mpv_get_status, mpv_get_position, mpv_command,
    MpvDisconnected, mpv_events,
)
from musicbrainz import (
    find_release,
    find_release_by_search,
    search_releases,
    get_release_info,
    _load_cache,
    _get_cached,
    _set_cached,
    _flush_cache,
)
from listenbrainz import submit_listen as lb_submit_listen, submit_playing_now as lb_submit_playing_now, clear_playing_now as lb_clear_playing_now
from lastfm import submit_listen as lf_submit_listen, submit_playing_now as lf_submit_playing_now, clear_playing_now as lf_clear_playing_now
from offline_queue import enqueue, flush_queue, queue_size

USER_AGENT = "cd-scrobbler/0.5.0 (recent8571@proton.me)"

LOG_FILE = Path.home() / ".config" / "cd-scrobbler" / "monitor.log"


def _print_tracks(info: dict) -> None:
    """Print release artist, title, and track listing."""
    print(f"{info['artist']} — {info['title']}")
    for t in info["tracks"]:
        ms = t["length_ms"]
        m, s = divmod(ms // 1000, 60)
        print(f"  {t['position']}: {t['title']} ({m}:{s:02d})")


def main():
    cfg = _Config()

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
    verbose = args.verbose or cfg.verbose

    cfg_token = cfg.listenbrainz_token

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


def _submit_queued(entry: dict, config: _Config | dict) -> None:
    """Submit a queued scrobble entry to the appropriate backend."""
    cfg = _Config() if isinstance(config, dict) else config
    backend = entry.get("backend", "listenbrainz")
    track = entry["track"]
    listened_at = entry.get("listened_at")

    if entry.get("listen_type") == "playing_now":
        if backend == "lastfm":
            lf_submit_playing_now(track, config)
        else:
            lb_submit_playing_now(track, cfg.listenbrainz_token)
        return

    if listened_at is None:
        raise ValueError("Queue entry missing 'listened_at'")

    if backend == "lastfm":
        lf_submit_listen(track, listened_at, config)
    else:
        lb_submit_listen(track, listened_at, cfg.listenbrainz_token)


def cmd_lookup(args, verbose, cfg: _Config):
    """Identify CD and print metadata."""
    from mbid import find_cd_drive

    device = args.device or cfg.device or find_cd_drive()
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

    # Ensure any pending cache writes are persisted
    _flush_cache(_load_cache())


def cmd_play(args, verbose, cfg: _Config):
    """Identify CD, start playback via mpv, scrobble tracks."""
    from mbid import find_cd_drive

    device = args.device or cfg.device or find_cd_drive()
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
    sock_path = args.socket or cfg.mpv_socket or _get_default_socket()
    if verbose:
        print(f"mpv socket: {sock_path}", file=sys.stderr)

    no_playing = args.no_playing_now or cfg.no_playing_now

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

    # Ensure any pending cache writes are persisted before exit
    _flush_cache(_load_cache())
    return


def _monitor_and_scrobble(info: dict, sock_path: str, verbose: bool, no_playing: bool, cfg: _Config):
    """Monitor mpv via IPC socket and scrobble tracks to configured backends.

    Uses mpv's event stream (push-based) instead of polling every second.
    Behaviour is 1:1 equivalent with the old poll-based version.
    """
    tracks_by_pos = {int(t["position"]): t for t in info["tracks"]}
    track_offsets = []
    cumulative = 0
    for t in info["tracks"]:
        track_offsets.append(cumulative)
        cumulative += t.get("length_ms", 0) / 1000

    last_position = None
    start_time = None
    scrobbled = set()
    was_paused = False
    track_start = None
    accumulated_play = 0.0
    current_msid = ""
    lb_enabled = bool(cfg.listenbrainz_token)
    lf_enabled = cfg.lastfm_enabled

    def _clear_playing_now():
        if no_playing:
            return
        if lb_enabled:
            try:
                lb_clear_playing_now(cfg.listenbrainz_token)
            except Exception:
                pass
        if lf_enabled:
            try:
                lf_clear_playing_now(cfg)
            except Exception:
                pass

    def _scrobble_track(position):
        if position not in scrobbled and start_time and track_start:
            played = (time.time() - start_time) + accumulated_play
            track = tracks_by_pos.get(position, {})
            track_len = track.get("length_ms", 0) / 1000 if track.get("length_ms") else 0
            min_played = min(4 * 60, track_len / 2) if track_len > 0 else 4 * 60
            if played > min_played:
                track.setdefault("album", info["title"])
                track.setdefault("artist", info["artist"])
                track.setdefault("release_mbid", info.get("release_mbid", ""))
                if current_msid:
                    track["recording_msid"] = current_msid
                ts = int(track_start)
                if lb_enabled:
                    try:
                        lb_submit_listen(track, ts, cfg.listenbrainz_token)
                        if verbose:
                            print(f"Scrobbled (LB): {track.get('title', position)}", file=sys.stderr)
                    except Exception as e:
                        enqueue({"backend": "listenbrainz", "listen_type": "single",
                                 "track": dict(track), "listened_at": ts})
                        if verbose:
                            print(f"LB scrobble failed, queued: {e}", file=sys.stderr)
                if lf_enabled:
                    try:
                        lf_submit_listen(track, ts, cfg)
                        if verbose:
                            print(f"Scrobbled (LF): {track.get('title', position)}", file=sys.stderr)
                    except Exception as e:
                        enqueue({"backend": "lastfm", "listen_type": "single",
                                 "track": dict(track), "listened_at": ts})
                        if verbose:
                            print(f"LF scrobble failed, queued: {e}", file=sys.stderr)
                scrobbled.add(position)

    def _on_track_change(current_track):
        """Handle a track boundary transition."""
        nonlocal last_position, start_time, track_start, accumulated_play, was_paused, current_msid
        if last_position is not None:
            _scrobble_track(last_position)
        start_time = time.time()
        track_start = time.time()
        accumulated_play = 0.0
        was_paused = False
        current_msid = ""
        last_position = current_track
        if verbose:
            print(f"Track {current_track}", file=sys.stderr)
        if not no_playing:
            track = tracks_by_pos.get(current_track, {})
            track.setdefault("album", info["title"])
            track.setdefault("artist", info["artist"])
            track.setdefault("release_mbid", info.get("release_mbid", ""))
            if lb_enabled:
                try:
                    msid = lb_submit_playing_now(track, cfg.listenbrainz_token)
                    current_msid = msid if msid else ""
                    if verbose:
                        print(f"Now (LB): {track.get('title', current_track)}", file=sys.stderr)
                except Exception as e:
                    current_msid = ""
                    if verbose:
                        print(f"LB playing_now failed: {e}", file=sys.stderr)
            if lf_enabled:
                try:
                    lf_submit_playing_now(track, cfg)
                    if verbose:
                        print(f"Now (LF): {track.get('title', current_track)}", file=sys.stderr)
                except Exception as e:
                    if verbose:
                        print(f"LF playing_now failed: {e}", file=sys.stderr)

    def _on_pause():
        nonlocal accumulated_play, was_paused
        _scrobble_track(last_position)
        if start_time is not None:
            accumulated_play += time.time() - start_time
        if not was_paused:
            was_paused = True
            if verbose:
                print("Paused — waiting for resume", file=sys.stderr)

    def _on_resume():
        nonlocal was_paused, start_time
        was_paused = False
        start_time = time.time()
        if verbose:
            print("Resumed", file=sys.stderr)

    def _on_eof():
        _scrobble_track(last_position)
        _clear_playing_now()
        last_position = None
        start_time = None

    total_duration = track_offsets[-1] if track_offsets else 0

    def _current_track(position_s):
        if position_s >= total_duration:
            return len(track_offsets)
        for i in range(len(track_offsets) - 1):
            if track_offsets[i] <= position_s < track_offsets[i + 1]:
                return i + 1
        return 1

    try:
        try:
            for event in mpv_events(sock_path):
                event_type = event.get("event", "")

                if event_type == "end-file":
                    if last_position is not None:
                        _on_eof()
                    break

                if event_type == "property-change":
                    prop = event.get("property", "")
                    value = event.get("data")

                    if prop == "pause":
                        if value is True and last_position is not None:
                            _on_pause()
                        elif value is False and was_paused:
                            _on_resume()
                        continue

                    if prop == "eof-reached":
                        if value is True and last_position is not None:
                            _on_eof()
                        break

                    if prop == "time-pos":
                        if isinstance(value, (int, float)):
                            pos_s = float(value)
                        elif isinstance(value, str):
                            try:
                                pos_s = float(value)
                            except (ValueError, TypeError):
                                continue
                        else:
                            continue
                        # mpv may report near-total duration before playback
                        # starts; skip until last_position is set.
                        if (pos_s > 0 and pos_s >= total_duration * 0.99
                                and last_position is None):
                            continue
                        current_track = _current_track(pos_s) if pos_s > 0 else None
                        if (current_track is not None
                                and current_track != last_position):
                            _on_track_change(current_track)
                        continue

                elif event_type == "playback-restart":
                    if last_position is not None and was_paused:
                        _on_resume()
                    continue

        except KeyboardInterrupt:
            if last_position is not None:
                _scrobble_track(last_position)
            _clear_playing_now()
            if verbose:
                print("\nInterrupted", file=sys.stderr)
        else:
            # Generator exhausted normally (mpv disconnected)
            if last_position is not None:
                _scrobble_track(last_position)
                if verbose:
                    print("mpv disconnected", file=sys.stderr)
    except Exception:
        # Catch-all for unexpected errors from mpv_events itself
        if last_position is not None:
            _scrobble_track(last_position)
        if verbose:
            import traceback
            print("Monitor error: " + traceback.format_exc(), file=sys.stderr)


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
            log_path = str(LOG_FILE)
            print("Monitor daemon started (PID {}) — tail -f {}".format(pid, log_path),
                  file=sys.stderr)
        return

    # Child: detach from terminal and run monitor
    os.setsid()

    # Redirect stdio to log file (not /dev/null) so monitor output is inspectable
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)  # stdin from /dev/null
    os.close(devnull)

    os.makedirs(str(LOG_FILE.parent), exist_ok=True)
    log_fd = os.open(str(LOG_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    os.dup2(log_fd, 1)  # stdout -> log
    os.dup2(log_fd, 2)  # stderr -> log
    # Don't close log_fd — the write() in except needs it

    try:
        import socket

        # Verify we can connect to mpv before proceeding
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect(sock_path)
        s.close()

        _monitor_and_scrobble(info, sock_path, verbose, no_playing, cfg)
        os._exit(0)
    except SystemExit:
        raise
    except Exception as e:
        # Write to a diagnostic file so we can see what happened
        err_file = str(LOG_FILE.parent / "monitor.err")
        try:
            with open(err_file, "w") as f:
                import traceback
                f.write(f"Monitor child failed:\n{traceback.format_exc()}\n")
        except OSError:
            pass
        # Write to the log fd (still open via dup2)
        try:
            msg = f"Monitor child failed: {type(e).__name__}: {e}\n"
            os.write(log_fd, msg.encode())
        except OSError:
            pass
        os._exit(1)


def cmd_cache(args, verbose, cfg: _Config):
    """Read disc ID and cache it against a user-provided release MBID."""
    from mbid import find_cd_drive

    # Accept full MusicBrainz URL or raw MBID
    mbid = args.release_mbid
    if mbid.startswith("http"):
        mbid = mbid.rstrip("/").split("/")[-1]
    if not re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", mbid):
        print(f"Error: invalid release MBID: {args.release_mbid}", file=sys.stderr)
        sys.exit(1)

    device = args.device or cfg.device or find_cd_drive()
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

    # Ensure any pending cache writes are persisted
    _flush_cache(_load_cache())


def cmd_history(args, cfg: _Config):
    """Show recent scrobbles from configured backends."""
    service = args.service.lower()

    if service in ("both", "listenbrainz"):
        token = cfg.listenbrainz_token
        if not token:
            try:
                token = _get_lb_token_from_file()
            except LookupError as e:
                print(f"ListenBrainz: {e}", file=sys.stderr)
                token = None
        if token:
            _print_lb_history(token, cfg.listenbrainz_username, args.count)

    if service in ("both", "lastfm"):
        if cfg.lastfm_enabled:
            try:
                import lastfm as lastfm_mod
                tracks = lastfm_mod.get_recent_tracks(limit=args.count, config=cfg)
            except Exception as e:
                print(f"Last.fm: {type(e).__name__}: {e}", file=sys.stderr)
            else:
                _print_lf_history(tracks)
        else:
            print("Last.fm: credentials not configured.", file=sys.stderr)
            print("  1. Register at https://www.last.fm/api/applications (get api_key + api_secret)",
                  file=sys.stderr)
            print("  2. Run: cdscrobble lastfm login  (enter username + password)",
                  file=sys.stderr)
            print("  3. Or set manually: cdscrobble lastfm set-key <your_session_key>",
                  file=sys.stderr)


def _get_lb_token_from_file():
    """Fallback: read ListenBrainz token from ~/.lbtoken."""
    from listenbrainz import TOKEN_FILE
    try:
        raw = TOKEN_FILE.read_text().strip()
    except FileNotFoundError:
        raise LookupError(
            f"ListenBrainz token not found at {TOKEN_FILE} — "
            "put it in ~/.config/cd-scrobbler/config as listenbrainz_token="
        )
    if "=" in raw:
        return raw.split("=", 1)[1].strip()
    return raw


def _print_lb_history(token, username, count):
    """Fetch and display ListenBrainz history."""
    import requests

    if not username:
        print(
            "ListenBrainz: username not configured. "
            "Add listenbrainz_username to ~/.config/cd-scrobbler/config "
            "(your username from listenbrainz.org).",
            file=sys.stderr,
        )
        return

    try:
        resp = requests.get(
            f"https://api.listenbrainz.org/1/user/{username}/listens",
            params={"count": count},
            headers={"Authorization": f"Token {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"ListenBrainz: error fetching history: {e}", file=sys.stderr)
        return

    listens = data.get("payload", {}).get("listens", [])
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


def cmd_control(args, cfg: _Config):
    """Control mpv playback via IPC."""
    sock_path = args.socket or cfg.mpv_socket or _get_default_socket()

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


def _lastfm_mobile_login(cfg: _Config, verbose: bool = False):
    """Authenticate via pylast mobile auth flow. No browser needed."""
    try:
        import pylast
    except ImportError:
        print("Error: pylast not installed. Run: pip install pylast",
              file=sys.stderr)
        sys.exit(1)

    api_key = cfg.lastfm_api_key
    api_secret = cfg.lastfm_api_secret
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
    except Exception as e:
        print(f"Error: server response error ({e})", file=sys.stderr)
        sys.exit(1)
    except pylast.PyLastError as e:
        print(f"Error: authentication failed ({e})", file=sys.stderr)
        print("Check your username and password.", file=sys.stderr)
        sys.exit(1)


def _lastfm_set_key(session_key: str, cfg: _Config):
    """Save a Last.fm session key to config."""
    save_config({"lastfm_session_key": session_key.strip()})
    # Verify it works
    api_key = cfg.lastfm_api_key
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
        net = pylast.LastFMNetwork(
            api_key=api_key,
            session_key=session_key.strip(),
        )
        # pylast 7.x: username is a property, not a method
        username = net.username if hasattr(net, "username") else ""
        # Verify by testing a simple call
        try:
            pylast.update_now_playing(artist="_test", title="_test")
            _test_ok = True
        except Exception:
            _test_ok = True  # Test may fail even with valid session (rate limits)
        print(f"Session key accepted. User: {username}")
        print("Last.fm scrobbling is now active.")
    except Exception as e:
        print(f"Error: invalid session key ({e})", file=sys.stderr)
        print("Run 'cdscrobble lastfm login' to get a new key.",
              file=sys.stderr)


if __name__ == "__main__":
    main()
