"""CD reading: disc ID, TOC, and CD-Text."""

import argparse
import discid
import glob
import re
import subprocess
import sys

DISC_ID_RE = re.compile(r"^[A-Za-z0-9_\-=\.\-]+$")


def find_cd_drive() -> str:
    """Find the first CD/DVD drive with a readable disc.

    Scans /dev/sr* and returns the first device discid can read.
    Raises DiscReadError if no drive with a disc is found.
    """
    drives = sorted(glob.glob("/dev/sr*"))
    errors = []
    for dev in drives:
        try:
            d = discid.read(device=dev)
            if len(d.tracks) > 0:
                return dev
        except Exception as e:
            errors.append(f"{dev}: {e}")
    raise DiscReadError(
        f"No CD drive with disc found. Tried: {', '.join(errors) or 'none'}"
    )


def get_disc_info(device: str | None = None) -> tuple[str, str]:
    """Read the CD and return (disc_id, toc_string).

    If *device* is None, auto-detects the first drive with a readable disc.
    Raises DiscReadError on failure.
    """
    if device is None:
        device = find_cd_drive()
    try:
        d = discid.read(device=device)
    except discid.DiscError as e:
        raise DiscReadError(f"Cannot read disc ({device}): {e}") from e
    except Exception as e:
        raise DiscReadError(f"Failed to read disc: {e}") from e

    if not DISC_ID_RE.fullmatch(d.id):
        raise DiscReadError(f"Invalid disc ID format: {d.id!r}")

    if len(d.tracks) == 0:
        raise DiscReadError("No tracks found on disc")

    return d.id, d.toc_string


def get_cdtext(device: str | None = None) -> dict:
    """Read CD-Text from the disc via cd-info.

    If *device* is None, auto-detects the first drive with a readable disc.
    Returns dict with keys: title, performer, tracks (list of str).
    Empty strings/list if no CD-Text or cd-info unavailable.
    """
    if device is None:
        device = find_cd_drive()
    try:
        result = subprocess.run(
            ["cd-info", device], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"title": "", "performer": "", "tracks": []}

    title = ""
    performer = ""
    tracks: list[str] = []
    in_track = False

    for line in result.stdout.splitlines():
        stripped = line.strip()
        if "CD-TEXT for Track" in line:
            in_track = True
            continue
        if in_track and stripped.startswith("TITLE:"):
            tracks.append(stripped.split(":", 1)[1].strip())
            in_track = False
        elif not in_track and stripped.startswith("TITLE:") and not title:
            title = stripped.split(":", 1)[1].strip()
        elif not in_track and stripped.startswith("PERFORMER:"):
            performer = stripped.split(":", 1)[1].strip()

    return {"title": title, "performer": performer, "tracks": tracks}


class DiscReadError(RuntimeError):
    """Raised when the CD cannot be read."""


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--device", default=None,
                        help="CD/DVD device (default: auto-detect)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    try:
        disc_id, toc = get_disc_info(args.device)
        if args.verbose:
            print(f"DEVICE: {args.device or find_cd_drive()}", file=sys.stderr)
            print(f"DISC_ID: {disc_id}", file=sys.stderr)
            print(f"TOC: {toc}", file=sys.stderr)
        print(disc_id)
    except DiscReadError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
