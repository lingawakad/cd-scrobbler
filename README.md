# cd-scrobbler

Scrobble CD playback to ListenBrainz with mpv.

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
cdscrobble history             # show recent scrobbles from ListenBrainz
cdscrobble queue               # show offline queue status
cdscrobble queue --flush       # attempt to flush the offline queue
cdscrobble control pause        # pause mpv playback
cdscrobble control play         # resume mpv playback
cdscrobble control stop         # stop mpv playback
```

All commands support `-d` (device, default auto-detected), `-v` (verbose), and `--no-playing-now` (play only).

## Config

Create `~/.config/cd-scrobbler/config`:

```ini
[general]
device = /dev/sr1
verbose = true
mpv_socket = /tmp/mpvsocket
no_playing_now = false
listenbrainz_token = your-token-here
```

CLI arguments override config file values.

## Features

- Disc ID lookup with MusicBrainz, CD-Text fallback
- Automatic cache for disc ID → release MBID mapping
- 20 MiB read-ahead buffer for scratched/problematic discs
- Direct mpv IPC control (no playerctl/MPRIS dependency)
- Offline queue: failed scrobbles retry on next run
- Ctrl+C gracefully scrobbles the current track
- History command: view recent scrobbles from ListenBrainz
- Cache capped at 500 entries (FIFO eviction)

## Commands

### `lookup`

Reads the disc ID from the CD drive (`/dev/sr0` by default), looks it up on MusicBrainz, and prints the release and track listing.

### `play`

Identifies the CD, then uses `mpv` for playback with direct IPC control. Scrobbles each track to ListenBrainz when playback ends. Exits when playback stops. Pausing scrobbles the current track — resume and it picks up the next one.

With `--start`, launches mpv with `--input-ipc-server` for direct communication (no MPRIS/playerctl needed).

### `cache`

Manually associates the disc ID of the current CD with a MusicBrainz release ID. Useful for unofficial releases, self-burned CDs, or any disc not in the MusicBrainz disc database. Once cached, `lookup` and `play` will find it automatically. Accepts a raw MBID or a full MusicBrainz release URL.

```bash
cdscrobble cache b4296301-7ab9-4fa1-a1d7-18647b7f94a4
cdscrobble cache https://musicbrainz.org/release/b4296301-7ab9-4fa1-a1d7-18647b7f94a4
```

### `history`

Shows your recent scrobbles from ListenBrainz. Defaults to 10 entries; use `-n` to change.

```bash
cdscrobble history
cdscrobble history -n 25
```

### `queue`

Shows the offline queue status. Use `--flush` to attempt submitting any queued scrobbles immediately.

```bash
cdscrobble queue
cdscrobble queue --flush
```

### `control`

Pause, resume, or stop mpv playback via the IPC socket. Requires mpv to be running with `--input-ipc-server` (automatic when using `play --start`).

```bash
cdscrobble control pause
cdscrobble control play
cdscrobble control stop
```

Use `--socket` to specify the socket path if it differs from the default.

## Dependencies

- `musicbrainzngs` — MusicBrainz API
- `discid` — CD disc ID reading
- `requests` — ListenBrainz API
- `mpv` — CD playback with read-ahead buffer (system package)
- `cd-info` — CD-Text fallback (system package, optional)
