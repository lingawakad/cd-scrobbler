"""MusicBrainz lookup: disc ID search, name search, release info."""

import argparse
import json
import musicbrainzngs
import re
import socket
import sys
from pathlib import Path

DISC_ID_RE = re.compile(r"^[A-Za-z0-9_\-=\.]+$")
MBID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

CACHE_DIR = Path.home() / ".cache" / "cd-scrobbler"
CACHE_FILE = CACHE_DIR / "musicbrainz.json"
_toc_score_cache: dict[str, float] = {}


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def _get_cached(key: str, cache: dict):
    return cache.get(key)


def _set_cached(key: str, value, cache: dict) -> None:
    cache[key] = value
    _save_cache(cache)


def _init():
    musicbrainzngs.set_useragent("cd-scrobbler", "0.1", "recent8571@proton.me")


def find_releases(disc_id: str, toc: str | None = None) -> list[dict]:
    """Find releases for a disc ID.

    Returns list of dicts: mbid, title, artist, disambiguation, packaging.
    Raises ValueError if disc_id format is invalid.
    Raises LookupError if no release found or only a CD stub exists.
    """
    if not DISC_ID_RE.fullmatch(disc_id):
        raise ValueError(f"Invalid disc ID format: {disc_id!r}")

    cache = _load_cache()
    key = f"disc:{disc_id}"
    cached = _get_cached(key, cache)
    if cached:
        return cached

    _init()
    result = musicbrainzngs.get_releases_by_discid(
        disc_id, toc=toc, includes=["artists", "recordings"])

    if "disc" in result:
        releases = result["disc"].get("release-list", [])
    elif "cdstub" in result:
        raise LookupError(f"Only CD stub found for disc ID {disc_id}")
    else:
        releases = result.get("release-list", [])

    if not releases:
        raise LookupError(f"No MusicBrainz release found for disc ID {disc_id}")

    releases_list = [
        {
            "mbid": r["id"],
            "title": r.get("title", ""),
            "artist": (r.get("artist-credit") or [{}])[0].get("artist", {}).get("name", ""),
            "disambiguation": r.get("disambiguation", ""),
            "packaging": r.get("packaging", ""),
        }
        for r in releases
    ]

    _set_cached(key, releases_list, cache)
    return releases_list


def _parse_toc(toc: str) -> tuple[int, list[int]]:
    """Parse TOC string into (track_count, track_lengths_ms).

    TOC format: '1 <track_count> <leadout> <offset1> <offset2> ...'
    Each sector = 1/75 second ≈ 13.333 ms.
    """
    parts = list(map(int, toc.split()))
    leadout = parts[2]
    offsets = parts[3:]

    lengths = []
    for i in range(1, len(offsets)):
        lengths.append(int((offsets[i] - offsets[i - 1]) * 1000 / 75))
    if offsets:
        lengths.append(int((leadout - offsets[-1]) * 1000 / 75))

    return len(lengths), lengths


def _toc_score(release_mbid: str, toc_lengths: list[int]) -> float:
    """Score how well a release's tracks match TOC lengths."""
    if release_mbid in _toc_score_cache:
        return _toc_score_cache[release_mbid]

    info = get_release_info(release_mbid)
    rel_lengths = [t["length_ms"] for t in info["tracks"]]

    if len(rel_lengths) != len(toc_lengths):
        score = 0.0
    else:
        total_diff = sum(abs(a - b) for a, b in zip(rel_lengths, toc_lengths))
        total_len = sum(max(a, b) for a, b in zip(rel_lengths, toc_lengths))
        score = 1.0 if total_len == 0 else 1 - (total_diff / total_len)

    _toc_score_cache[release_mbid] = score
    return score


def find_release(disc_id: str, toc: str | None = None) -> dict:
    """Find the best release for a disc ID, using TOC to disambiguate.

    Returns single dict: mbid, title, artist, disambiguation, packaging.
    Raises ValueError if disc_id format is invalid.
    Raises LookupError if no release found or ambiguity can't be resolved.
    """
    candidates = find_releases(disc_id, toc=toc)
    if len(candidates) == 1:
        return candidates[0]

    if toc is None:
        raise LookupError(
            f"Ambiguous disc ID ({len(candidates)} matches) — provide TOC")

    _, toc_lengths = _parse_toc(toc)
    best = max(candidates, key=lambda c: _toc_score(c["mbid"], toc_lengths))
    best_score = _toc_score_cache.get(best["mbid"], 0)
    if best_score < 0.9:
        raise LookupError("Ambiguous disc ID — no confident TOC match")

    return best


def find_release_by_search(candidates: list[dict], track_titles: list[str]) -> dict:
    """Pick the best release from search results using CD-Text track titles.

    Returns single dict: mbid, title, artist, format.
    Raises LookupError if no confident match.
    """
    if len(candidates) == 1:
        return candidates[0]
    if not track_titles:
        raise LookupError(f"Ambiguous search ({len(candidates)} matches) — provide track titles")

    best = max(candidates, key=lambda c: _title_score(c["mbid"], track_titles))
    score = _title_score(best["mbid"], track_titles)

    if score < 0.7:
        raise LookupError("Ambiguous search — no confident track title match")

    return best


def _title_score(release_mbid: str, track_titles: list[str]) -> float:
    """Score how well a release's tracks match CD-Text titles."""
    info = get_release_info(release_mbid)
    release_titles = [t["title"].lower() for t in info["tracks"]]
    cd_titles = [t.lower() for t in track_titles]

    if len(release_titles) != len(cd_titles):
        return 0.0

    matches = sum(1 for a, b in zip(release_titles, cd_titles) if a == b)
    return matches / len(cd_titles)


def get_release_info(release_mbid: str) -> dict:
    """Return release metadata: title, artist, tracks.

    Returns dict with: release_mbid, title, artist, artist_mbid, tracks.
    Each track: position, title, length_ms, artist, artist_mbid, recording_mbid.
    Raises ValueError if MBID format is invalid.
    Raises LookupError if no release found.
    """
    if not MBID_RE.fullmatch(release_mbid):
        raise ValueError(f"Invalid release MBID format: {release_mbid!r}")

    cache = _load_cache()
    key = f"release:{release_mbid}"
    cached = _get_cached(key, cache)
    if cached:
        return cached

    _init()
    data = musicbrainzngs.get_release_by_id(
        release_mbid, includes=["recordings", "artists", "artist-credits"])
    release = data.get("release")
    if not release:
        raise LookupError(f"No release found for MBID {release_mbid}")

    ac = release.get("artist-credit", [])
    artists = []
    for a in ac:
        if "artist" in a:
            artists.append(a["artist"]["name"])
        elif "name" in a:
            artists.append(a["name"])
    artist = ", ".join(artists) if artists else ""
    artist_mbid = ac[0]["artist"]["id"] if ac and "artist" in ac[0] else ""

    tracks = []
    for medium in release.get("medium-list", []):
        for track in medium.get("track-list", []):
            rec = track.get("recording", {})
            rac = rec.get("artist-credit", [])
            tracks.append({
                "position": track["position"],
                "title": rec.get("title", ""),
                "length_ms": int(track.get("length") or 0),
                "artist": rac[0]["artist"]["name"] if rac else artist,
                "artist_mbid": rac[0]["artist"]["id"] if rac else artist_mbid,
                "recording_mbid": rec.get("id", ""),
            })

    result = {
        "release_mbid": release_mbid,
        "title": release.get("title", ""),
        "artist": artist,
        "artist_mbid": artist_mbid,
        "tracks": tracks,
    }

    _set_cached(key, result, cache)
    return result


def search_releases(query: str, limit: int = 5) -> list[dict]:
    """Search releases by query string (e.g. 'Artist Album').

    Returns list of dicts: mbid, title, artist, format.
    """
    cache = _load_cache()
    key = f"search:{query}"
    cached = _get_cached(key, cache)
    if cached:
        return cached

    _init()
    result = musicbrainzngs.search_releases(query=query, limit=limit)

    releases_list = [
        {
            "mbid": r["id"],
            "title": r.get("title", ""),
            "artist": (r.get("artist-credit") or [{}])[0].get("artist", {}).get("name", ""),
            "format": r.get("format", ""),
        }
        for r in result.get("release-list", [])
    ]

    _set_cached(key, releases_list, cache)
    return releases_list


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("disc_id", nargs="?", default=None,
                        help="MusicBrainz disc ID (Base32)")
    parser.add_argument("-t", "--toc", default=None, help="TOC string")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    try:
        if args.disc_id:
            releases = find_releases(args.disc_id, toc=args.toc)
            if args.verbose:
                print(f"Disc ID: {args.disc_id}", file=sys.stderr)
                print(f"Matches: {len(releases)}", file=sys.stderr)
            for r in releases:
                print(f"{r['mbid']} | {r['title']} | {r['artist']} | "
                      f"{r['disambiguation']} | {r['packaging']}")
    except (ValueError, LookupError) as e:
        print(e, file=sys.stderr)
        sys.exit(1)
