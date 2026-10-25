# cd-scrobbler

Scrobble CD playback to ListenBrainz.

## Install

```bash
# System dependencies (Fedora example)
sudo dnf install playerctl cdda2wav  # cd-info comes from cdda2wav

pip install cd-scrobbler
# or from source:
pip install ./cd-scrobbler
```

## Setup

Get a ListenBrainz token from https://listenbrainz.org/settings/ and save it:

```bash
echo "your-token-here" > ~/.lbtoken
```

## Usage

```
cdscrobble lookup   # identify CD and print metadata
cdscrobble play     # identify CD, start playback, scrobble
cdscrobble play --start    # same, but start mpv playback
```

All commands support `-v` for verbose output and `--no-playing-now` to skip playing_now notifications.

## Commands

### `lookup`

Reads the disc ID from the CD drive (`/dev/sr0` by default), looks it up on MusicBrainz, and prints the release and track listing.

### `play`

Identifies the CD, then uses `mpv` for playback and `playerctl` to track position. Scrobbles each track to ListenBrainz when playback ends. Exits when playback stops. Pausing scrobbles the current track — resume and it picks up the next one.

## Dependencies

- `musicbrainzngs` — MusicBrainz API
- `discid` — CD disc ID reading
- `requests` — ListenBrainz API
- `playerctl` — MPRIS playback monitoring (system package)
- `mpv` — CD playback with read-ahead buffer (system package)
- `cd-info` — CD-Text fallback (system package, optional)
