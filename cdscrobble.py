"""cdscrobble — Scrobble CD playback to ListenBrainz."""

import argparse
import subprocess
import sys
import time

from mbid import get_disc_info, get_cdtext, DiscReadError
from musicbrainz import (
    find_release,
    find_release_by_search,
    search_releases,
    get_release_info,
)
from listenbrainz import submit_listen, submit_playing_now


def main():
    parser = argparse.ArgumentParser(
        description="Scrobble CD playback to ListenBrainz",
    )
    sub = parser.add_subparsers(dest="command")

    # lookup: identify CD and print metadata
    p_lookup = sub.add_parser("lookup", help="Identify CD and print metadata")
    p_lookup.add_argument("-d", "--device", default="/dev/sr0")
    p_lookup.add_argument("-v", "--verbose", action="store_true")

    # play: identify CD, start playback, monitor, scrobble
    p_play = sub.add_parser("play", help="Identify CD, start playback, scrobble")
    p_play.add_argument("-d", "--device", default="/dev/sr0")
    p_play.add_argument("-v", "--verbose", action="store_true")
    p_play.add_argument("--start", action="store_true",
                        help="Start playback after lookup (playerctl play)")
    p_play.add_argument("--no-playing-now", action="store_true",
                        help="Skip playing_now notifications")

    # scrobble: monitor player and submit listens (standalone)
    p_scrob = sub.add_parser("scrobble", help="Monitor playback and scrobble")
    p_scrob.add_argument("-v", "--verbose", action="store_true")
    p_scrob.add_argument("--no-playing-now", action="store_true",
                         help="Skip playing_now notifications")

    args = parser.parse_args()

    if args.command == "lookup":
        cmd_lookup(args)
    elif args.command == "play":
        cmd_play(args)
    elif args.command == "scrobble":
        cmd_scrobble(args)
    else:
        parser.print_help()
        sys.exit(1)


def cmd_lookup(args):
    """Identify CD and print metadata."""
    from musicbrainz import _load_cache, _get_cached, _set_cached

    disc_id = None
    # Try disc ID lookup
    try:
        disc_id, toc = get_disc_info(args.device)
        if args.verbose:
            print(f"Disc ID: {disc_id}", file=sys.stderr)

        # Check cache for disc ID → release MBID
        cache = _load_cache()
        disc_key = f"disc:{disc_id}"
        cached_mbid = _get_cached(disc_key, cache)
        if cached_mbid:
            if args.verbose:
                print("Cache hit (disc ID)", file=sys.stderr)
            info = get_release_info(cached_mbid)
            print(f"{info['artist']} — {info['title']}")
            for t in info["tracks"]:
                ms = t["length_ms"]
                m, s = divmod(ms // 1000, 60)
                print(f"  {t['position']}: {t['title']} ({m}:{s:02d})")
            return

        release = find_release(disc_id, toc=toc)
        if args.verbose:
            print(f"Match (disc ID): {release['artist']} — {release['title']}",
                  file=sys.stderr)
    except DiscReadError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        if args.verbose:
            print(f"Disc ID lookup failed: {e}", file=sys.stderr)
            print("Falling back to CD-Text search...", file=sys.stderr)
        release = _lookup_by_cdtext(args)
        if release is None:
            sys.exit(1)

        # Cache disc ID → release MBID
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


def cmd_play(args):
    """Identify CD, monitor playback via playerctl, scrobble tracks."""
    from musicbrainz import _load_cache, _get_cached, _set_cached

    # Step 1: Read disc ID (fast)
    try:
        disc_id, toc = get_disc_info(args.device)
        if args.verbose:
            print(f"Disc ID: {disc_id}", file=sys.stderr)
    except DiscReadError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Step 2: Check cache for disc ID → release MBID
    cache = _load_cache()
    disc_key = f"disc:{disc_id}"
    cached_mbid = _get_cached(disc_key, cache)
    if cached_mbid:
        if args.verbose:
            print("Cache hit (disc ID)", file=sys.stderr)
        try:
            info = get_release_info(cached_mbid)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        if args.verbose:
            print(f"{info['artist']} — {info['title']}", file=sys.stderr)
            print(f"Tracks: {len(info['tracks'])}", file=sys.stderr)
    else:
        # Step 3: Lookup CD (API calls)
        try:
            release = find_release(disc_id, toc=toc)
        except Exception as e:
            if args.verbose:
                print(f"Disc ID lookup failed: {e}", file=sys.stderr)
            release = _lookup_by_cdtext(args)
            if release is None:
                sys.exit(1)

        # Cache disc ID → release MBID
        _set_cached(disc_key, release["mbid"], cache)

        # Get track info
        try:
            info = get_release_info(release["mbid"])
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        if args.verbose:
            print(f"{info['artist']} — {info['title']}", file=sys.stderr)
            print(f"Tracks: {len(info['tracks'])}", file=sys.stderr)

    # Start playback if requested
    if args.start:
        subprocess.Popen(["mpv", "--demuxer-max-bytes=5MiB", "--ao=pipewire", "--gapless-audio", "cdda://"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if args.verbose:
            print("Playback started", file=sys.stderr)

    # Step 3: Monitor playback and scrobble
    _monitor_and_scrobble(info, args)


def cmd_scrobble(args):
    """Monitor Strawberry via playerctl and scrobble tracks."""
    from listenbrainz import submit_listen, submit_playing_now

    # Poll playerctl metadata for track changes
    last_title = None
    last_artist = None
    start_time = None
    scrobbled = set()  # track positions already scrobbled

    while True:
        try:
            result = subprocess.run(
                ["playerctl", "metadata", "xesam:title", "xesam:artist",
                 "mpris:trackid", "mpris:length", "player-playback-status"],
                capture_output=True, text=True, timeout=5,
            )
            meta = _parse_metadata(result.stdout.strip())

            title = meta.get("xesam:title", "")
            artist = meta.get("xesam:artist", "")
            length = int(meta.get("mpris:length", 0))
            status = meta.get("player-playback-status", "")

            # Detect playback stopped — scrobble current track and clear state
            if status == "Stopped" and last_title and last_title not in scrobbled:
                if start_time and (time.time() - start_time) > 4 * 60:
                    if args.verbose:
                        print(f"Scrobble: {last_artist} — {last_title}", file=sys.stderr)
                    try:
                        submit_listen({
                            "artist": last_artist,
                            "title": last_title,
                            "length_ms": length,
                        }, int(start_time))
                    except Exception as e:
                        if args.verbose:
                            print(f"Scrobble failed: {e}", file=sys.stderr)
                    scrobbled.add(last_title)
                last_title = None
                last_artist = None
                start_time = None
                continue

            # Detect pause — scrobble current track, preserve state for resume
            if status == "Paused" and last_title and last_title not in scrobbled:
                if start_time and (time.time() - start_time) > 4 * 60:
                    if args.verbose:
                        print(f"Scrobble (paused): {last_artist} — {last_title}", file=sys.stderr)
                    try:
                        submit_listen({
                            "artist": last_artist,
                            "title": last_title,
                            "length_ms": length,
                        }, int(start_time))
                    except Exception as e:
                        if args.verbose:
                            print(f"Scrobble failed: {e}", file=sys.stderr)
                    scrobbled.add(last_title)
                start_time = None
                continue

            # Detect resume from pause
            if status == "Playing" and last_title and last_title not in scrobbled and start_time is None:
                start_time = time.time()

            # Detect track change
            if title and (title != last_title or artist != last_artist):
                if last_title and last_title not in scrobbled:
                    # Scrobble previous track if played long enough
                    if start_time and (time.time() - start_time) > 4 * 60:
                        if args.verbose:
                            print(f"Scrobble: {last_artist} — {last_title}",
                                  file=sys.stderr)
                        try:
                            submit_listen({
                                "artist": last_artist,
                                "title": last_title,
                                "length_ms": length,
                            }, int(start_time))
                        except Exception as e:
                            if args.verbose:
                                print(f"Scrobble failed: {e}", file=sys.stderr)
                        scrobbled.add(last_title)

                start_time = time.time()
                last_title = title
                last_artist = artist

                # Submit playing_now
                if not args.no_playing_now:
                    if args.verbose:
                        print(f"Now: {artist} — {title}", file=sys.stderr)
                    try:
                        submit_playing_now({
                            "artist": artist,
                            "title": title,
                        })
                    except Exception as e:
                        if args.verbose:
                            print(f"playing_now failed: {e}", file=sys.stderr)

        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            pass

        time.sleep(2)


def _parse_metadata(text: str) -> dict:
    """Parse playerctl metadata output into a dict."""
    result = {}
    for line in text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def _monitor_and_scrobble(info: dict, args):
    """Monitor playerctl and scrobble tracks using known track list."""
    from listenbrainz import submit_listen, submit_playing_now

    tracks_by_pos = {int(t["position"]): t for t in info["tracks"]}
    # Calculate track offsets (cumulative) in microseconds
    track_offsets = []
    cumulative = 0
    for t in info["tracks"]:
        track_offsets.append(cumulative)
        cumulative += t.get("length_ms", 0) * 1000  # ms -> us
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
                    submit_listen(track, int(start_time))
                    if args.verbose:
                        print(f"Scrobbled: {track.get('title', position)}", file=sys.stderr)
                    scrobbled.add(position)
                except Exception as e:
                    if args.verbose:
                        print(f"Scrobble failed: {e}", file=sys.stderr)

    def _current_track(position_us):
        """Map position (us) to track number."""
        for i in range(len(track_offsets) - 1):
            if track_offsets[i] <= position_us < track_offsets[i + 1]:
                return i + 1  # track positions are 1-indexed
        return len(track_offsets)  # last track or beyond

    while True:
        try:
            # Get playback status and position
            status_result = subprocess.run(
                ["playerctl", "-p", "mpv", "status"],
                capture_output=True, text=True, timeout=5,
            )
            pos_result = subprocess.run(
                ["playerctl", "-p", "mpv", "position"],
                capture_output=True, text=True, timeout=5,
            )
            status = status_result.stdout.strip()
            pos_us = 0
            try:
                pos_val = pos_result.stdout.strip().replace("us", "").strip()
                pos_us = int(float(pos_val) * 1_000_000)  # seconds -> microseconds
            except ValueError:
                pass

            current_track = _current_track(pos_us) if pos_us > 0 else None

            if args.verbose and current_track:
                print(f"Track {current_track}", file=sys.stderr)

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
                if args.verbose:
                    print("Paused — waiting for resume", file=sys.stderr)
                continue

            # Detect resume from pause
            if status == "Playing" and last_position is not None and start_time is None:
                start_time = time.time() - (pos_us / 1_000_000) if pos_us > 0 else time.time()
                if args.verbose:
                    print("Resumed", file=sys.stderr)

            # Detect track change
            if current_track is not None and current_track != last_position:
                # Scrobble previous track
                if last_position is not None:
                    _scrobble_track(last_position)

                # New track started — estimate start time from position
                start_time = time.time() - (pos_us / 1_000_000) if pos_us > 0 else time.time()
                last_position = current_track

                # Submit playing_now
                if not args.no_playing_now:
                    track = tracks_by_pos.get(current_track, {})
                    track.setdefault("album", info["title"])
                    track.setdefault("artist", info["artist"])
                    track.setdefault("release_mbid", info.get("release_mbid", ""))
                    try:
                        submit_playing_now(track)
                        if args.verbose:
                            print(f"Now: {track.get('title', current_track)}", file=sys.stderr)
                    except Exception as e:
                        if args.verbose:
                            print(f"playing_now failed: {e}", file=sys.stderr)

        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            pass

        time.sleep(2)


def _lookup_by_cdtext(args):
    """Fallback: search by CD-Text."""
    cdtext = get_cdtext(args.device)
    if not cdtext["performer"] or not cdtext["title"]:
        print("Error: No CD-Text found on disc", file=sys.stderr)
        return None

    query = f"{cdtext['performer']} {cdtext['title']}"
    if args.verbose:
        print(f"CD-Text: {query}", file=sys.stderr)

    # Check cache for CD-Text → release MBID
    from musicbrainz import _load_cache, _get_cached, _set_cached
    cache = _load_cache()
    ct_key = f"cdtext:{query}:{len(cdtext['tracks'])}"
    cached_mbid = _get_cached(ct_key, cache)
    if cached_mbid:
        if args.verbose:
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

    # Cache CD-Text → release MBID
    _set_cached(ct_key, release["mbid"], cache)

    if args.verbose:
        print(f"Match (CD-Text): {release['artist']} — {release['title']}",
              file=sys.stderr)
    return {"mbid": release["mbid"], **release}


if __name__ == "__main__":
    main()
