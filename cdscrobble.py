"""cdscrobble — Scrobble CD playback to ListenBrainz."""

import argparse
import json
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from config import load_config, get_device, is_verbose, get_mpv_socket, no_playing_now, get_listenbrainz_token
from mbid import get_disc_info, get_cdtext, DiscReadError
from musicbrainz import (
    find_release,
    find_release_by_search,
    search_releases,
    get_release_info,
)
from listenbrainz import submit_listen, submit_playing_now
from offline_queue import enqueue, flush_queue, queue_size


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

    # history: show recent scrobbles from ListenBrainz
    p_history = sub.add_parser("history", help="Show recent scrobbles")
    p_history.add_argument("-n", "--count", type=int, default=10,
                           help="Number of recent scrobbles to show (default: 10)")
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

    args = parser.parse_args()

    # Merge CLI args with config: CLI takes precedence
    verbose = args.verbose or is_verbose(cfg)

    cfg_token = get_listenbrainz_token(cfg)

    # Flush any pending queue entries at startup
    if hasattr(args, 'command') and args.command != "queue":
        try:
            ok, remaining = flush_queue(lambda e: _submit_queued(e, cfg_token))
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
        cmd_play(args, verbose, cfg, cfg_token)
    elif args.command == "cache":
        cmd_cache(args, verbose, cfg)
    elif args.command == "history":
        cmd_history(args)
    elif args.command == "queue":
        cmd_queue(args, verbose, cfg)
    elif args.command == "control":
        cmd_control(args, cfg)
    else:
        parser.print_help()
        sys.exit(1)


def _submit_queued(entry: dict, config_token=None) -> None:
    """Submit a queued scrobble entry."""
    listen_type = entry.get("listen_type", "single")
    if listen_type == "single":
        submit_listen(entry["track"], entry["listened_at"], config_token)
    elif listen_type == "playing_now":
        submit_playing_now(entry["track"], config_token)


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
            print(f"{info['artist']} — {info['title']}")
            for t in info["tracks"]:
                ms = t["length_ms"]
                m, s = divmod(ms // 1000, 60)
                print(f"  {t['position']}: {t['title']} ({m}:{s:02d})")
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
        print(f"{info['artist']} — {info['title']}")
        for t in info["tracks"]:
            ms = t["length_ms"]
            m, s = divmod(ms // 1000, 60)
            print(f"  {t['position']}: {t['title']} ({m}:{s:02d})")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_play(args, verbose, cfg, cfg_token=None):
    """Identify CD, start playback via mpv, scrobble tracks."""
    from mbid import DiscReadError, find_cd_drive
    from musicbrainz import _load_cache, _get_cached, _set_cached
    from mpv_ipc import (
        _get_default_socket, wait_for_mpv, is_mpv_ready,
        mpv_get_status, mpv_get_position,
    )

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

    # Monitor playback and scrobble
    _monitor_and_scrobble(info, sock_path, verbose, no_playing, cfg_token)


def _monitor_and_scrobble(info: dict, sock_path: str, verbose: bool, no_playing: bool, config_token=None):
    """Monitor mpv via IPC socket and scrobble tracks using known track list."""
    tracks_by_pos = {int(t["position"]): t for t in info["tracks"]}
    # Calculate track offsets (cumulative) in seconds
    track_offsets = []
    cumulative = 0
    for t in info["tracks"]:
        track_offsets.append(cumulative)
        cumulative += t.get("length_ms", 0) / 1000  # ms -> seconds
    last_position = None
    start_time = None
    scrobbled = set()  # positions already scrobbled

    def _scrobble_track(position):
        """Scrobble a track if played long enough."""
        nonlocal start_time
        if position not in scrobbled and start_time:
            played = time.time() - start_time
            track = tracks_by_pos.get(position, {})
            track_len = track.get("length_ms", 0) / 1000 if track.get("length_ms") else 0
            min_played = min(4 * 60, track_len / 2) if track_len > 0 else 4 * 60
            if played > min_played:
                track.setdefault("album", info["title"])
                track.setdefault("artist", info["artist"])
                track.setdefault("release_mbid", info.get("release_mbid", ""))
                try:
                    submit_listen(track, int(start_time), config_token)
                    if verbose:
                        print(f"Scrobbled: {track.get('title', position)}", file=sys.stderr)
                    scrobbled.add(position)
                except Exception as e:
                    # Queue for later retry
                    enqueue({
                        "listen_type": "single",
                        "track": dict(track),
                        "listened_at": int(start_time),
                    })
                    if verbose:
                        print(f"Scrobble failed, queued: {e}", file=sys.stderr)

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

                # Detect playback stopped — scrobble current track then exit
                if status == "Stopped" and last_position is not None:
                    _scrobble_track(last_position)
                    last_position = None
                    start_time = None
                    break

                # Detect pause — scrobble current track, stay alive for resume
                if status == "Paused" and last_position is not None:
                    _scrobble_track(last_position)
                    start_time = None
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
                if status == "Playing" and last_position is not None and start_time is None:
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
                    last_position = current_track

                    # Submit playing_now
                    if not no_playing:
                        track = tracks_by_pos.get(current_track, {})
                        track.setdefault("album", info["title"])
                        track.setdefault("artist", info["artist"])
                        track.setdefault("release_mbid", info.get("release_mbid", ""))
                        try:
                            submit_playing_now(track, config_token)
                            if verbose:
                                print(f"Now: {track.get('title', current_track)}", file=sys.stderr)
                        except Exception as e:
                            if verbose:
                                print(f"playing_now failed: {e}", file=sys.stderr)

            except ConnectionError:
                # mpv not connected yet — wait
                pass
            except Exception as e:
                if verbose:
                    print(f"Unexpected error in monitor loop: {e}", file=sys.stderr)

            time.sleep(1)

    except KeyboardInterrupt:
        if last_position is not None:
            _scrobble_track(last_position)
        if verbose:
            print("\nInterrupted", file=sys.stderr)


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


def cmd_history(args):
    """Show recent scrobbles from ListenBrainz."""
    from listenbrainz import _get_token

    try:
        token = _get_token()
    except LookupError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    import requests

    # Get user ID first
    try:
        user_resp = requests.get(
            "https://api.listenbrainz.org/1/me/username",
            headers={"Authorization": f"Token {token}"},
            timeout=10,
        )
        user_resp.raise_for_status()
        username = user_resp.json()["user_name"]
    except Exception as e:
        print(f"Error fetching username: {e}", file=sys.stderr)
        sys.exit(1)

    # Fetch recent listens
    try:
        resp = requests.get(
            f"https://api.listenbrainz.org/1/user/{username}/listens",
            params={"limit": args.count},
            headers={"Authorization": f"Token {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"Error fetching history: {e}", file=sys.stderr)
        sys.exit(1)

    listens = data.get("payload", [])
    if not listens:
        print("No recent scrobbles found.")
        return

    for listen in listens:
        meta = listen.get("track_metadata", {})
        artist = meta.get("artist_name", "Unknown")
        title = meta.get("track_name", "Unknown")
        album = meta.get("release_name", "")
        ts = listen.get("listened_at", 0)
        dt = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
        album_str = f" ({album})" if album else ""
        print(f"{dt}  {artist} — {title}{album_str}")


def cmd_queue(args, verbose, cfg):
    """Show or flush the offline queue."""
    size = queue_size()
    print(f"Queue: {size} pending entry/entries")

    if args.flush:
        cfg_token = get_listenbrainz_token(cfg)
        ok, remaining = flush_queue(lambda e: _submit_queued(e, cfg_token))
        if ok:
            print(f"Flushed: {ok} scrobble(s) submitted")
        if remaining:
            print(f"Remaining: {remaining} (will retry next run)")
        else:
            print("Queue is now empty")


def cmd_control(args, cfg):
    """Control mpv playback via IPC."""
    from mpv_ipc import _get_default_socket, mpv_command, mpv_get_property

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


if __name__ == "__main__":
    main()
