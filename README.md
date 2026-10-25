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
cdscrobble lookup              # identify CD and print metadata
cdscrobble play --start        # identify CD, start mpv playback, scrobble
cdscrobble cache <release-mbid>  # cache a release MBID for the current disc
```

All commands support `-d` (device, default `/dev/sr0`), `-v` (verbose), and `--no-playing-now` (play only).

## Commands

### `lookup`

Reads the disc ID from the CD drive (`/dev/sr0` by default), looks it up on MusicBrainz, and prints the release and track listing.

### `play`

Identifies the CD, then uses `mpv` for playback and `playerctl` to track position. Scrobbles each track to ListenBrainz when playback ends. Exits when playback stops. Pausing scrobbles the current track — resume and it picks up the next one.

### `cache`

Manually associates the disc ID of the current CD with a MusicBrainz release ID. Useful for unofficial releases, self-burned CDs, or any disc not in the MusicBrainz disc database. Once cached, `lookup` and `play` will find it automatically. Accepts a raw MBID or a full MusicBrainz release URL.

```bash
cdscrobble cache b4296301-7ab9-4fa1-a1d7-18647b7f94a4
cdscrobble cache https://musicbrainz.org/release/b4296301-7ab9-4fa1-a1d7-18647b7f94a4
```

## Dependencies

- `musicbrainzngs` — MusicBrainz API
- `discid` — CD disc ID reading
- `requests` — ListenBrainz API
- `playerctl` — MPRIS playback monitoring (system package)
- `mpv` — CD playback with read-ahead buffer (system package)
- `cd-info` — CD-Text fallback (system package, optional)
