# cd-scrobbler

Scrobble CD playback to ListenBrainz and Last.fm with mpv.

## Install

```bash
# System dependencies (Fedora example)
sudo dnf install playerctl cdda2wav  # cd-info comes from cdda2wav

pip install cd-scrobbler
# or from source:
pip install ./cd-scrobbler
```

## Setup

### ListenBrainz

Get a ListenBrainz token from https://listenbrainz.org/settings/ and save it:

```bash
echo "your-token-here" > ~/.lbtoken
```

### Last.fm (optional)

Get an API key from https://www.last.fm/api/application and add it to config:

```ini
[general]
lastfm_api_key = YOUR_API_KEY
```

Then run `cdscrobble lastfm login` to complete the authorization flow.

## Usage

```
cdscrobble lookup              # identify CD and print metadata
cdscrobble play --start        # identify CD, start mpv playback, scrobble
cdscrobble cache <release-mbid>  # cache a release MBID for the current disc
cdscrobble history             # show recent scrobbles (both backends)
cdscrobble history -s lastfm   # show only Last.fm scrobbles
cdscrobble history -s listenbrainz  # show only ListenBrainz scrobbles
cdscrobble queue               # show offline queue status
cdscrobble queue --flush       # attempt to flush the offline queue
cdscrobble control pause        # pause mpv playback
cdscrobble control play         # resume mpv playback
cdscrobble control stop         # stop mpv playback
cdscrobble lastfm login         # mobile auth (no browser needed)
cdscrobble lastfm set-key <key> # manually set session key
```

All commands support `-d` (device, default auto-detected), `-v` (verbose), and `--no-playing-now` (play only).

## Config

Copy the sample config, fill in your token, and move it to the right location:

```bash
cp config.sample ~/.config/cd-scrobbler/config
```

Edit `~/.config/cd-scrobbler/config` and set at minimum:

```ini
[general]
listenbrainz_token = your-token-here
```

See `config.sample` in this repo for all available options.

## Features

- Disc ID lookup with MusicBrainz, CD-Text fallback
- **Dual-backend scrobbling** to both ListenBrainz and Last.fm (each independently toggleable)
- Automatic cache for disc ID → release MBID mapping
- 20 MiB read-ahead buffer for scratched/problematic discs
- Direct mpv IPC control (no playerctl/MPRIS dependency)
- Offline queue: failed scrobbles to any backend retry on next run
- `playing_now` cleared when playback ends or is interrupted
- Ctrl+C gracefully scrobbles the current track
- History command: view recent scrobbles from any configured backend
- `lastfm login` command to complete OAuth session key flow
- Cache capped at 500 entries (FIFO eviction)
- Daemonized `play --start`: releases the prompt, logs to `~/.config/cd-scrobbler/monitor.log`
- pylast 7.x compatible (mobile auth, flat API)

## Commands

### `lookup`

Reads the disc ID from the CD drive (`/dev/sr0` by default), looks it up on MusicBrainz, and prints the release and track listing.

### `play`

Identifies the CD, then uses `mpv` for playback with direct IPC control. Scrobbles each track to ListenBrainz when playback ends. Exits when playback stops. Pausing scrobbles the current track — resume and it picks up the next one.

With `--start`, launches mpv in the background and daemonizes the monitor process so your terminal prompt is released immediately. Monitor output goes to `~/.config/cd-scrobbler/monitor.log` — tail it with:

```bash
tail -f ~/.config/cd-scrobbler/monitor.log
```

### `cache`

Manually associates the disc ID of the current CD with a MusicBrainz release ID. Useful for unofficial releases, self-burned CDs, or any disc not in the MusicBrainz disc database. Once cached, `lookup` and `play` will find it automatically. Accepts a raw MBID or a full MusicBrainz release URL.

```bash
cdscrobble cache b4296301-7ab9-4fa1-a1d7-18647b7f94a4
cdscrobble cache https://musicbrainz.org/release/b4296301-7ab9-4fa1-a1d7-18647b7f94a4
```

### `history`

Shows your recent scrobbles. Defaults to showing both backends; use `-s` to filter:

```bash
cdscrobble history
cdscrobble history -n 25
cdscrobble history -s lastfm
cdscrobble history -s listenbrainz
```

### `queue`

Shows the offline queue status. Use `-v` to see queued entries with timestamps and retry counts. Use `--flush` to attempt submitting any queued scrobbles immediately.

```bash
cdscrobble queue
cdscrobble queue -v
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

### `lastfm`

Manage Last.fm credentials:

```bash
cdscrobble lastfm login    # mobile auth — username + password, no browser
cdscrobble lastfm set-key <key>  # manually enter a session key
```

The login flow uses Last.fm mobile auth: enter your username and password, and the session key is obtained programmatically. No browser or manual copy-paste needed.

Both backends scrobble independently: scrobbling to ListenBrainz doesn't require Last.fm, and vice versa. Both are scrobbled simultaneously when both are configured.

## Dependencies

- `musicbrainzngs` — MusicBrainz API
- `discid` — CD disc ID reading
- `requests` — ListenBrainz API
- `pylast` — Last.fm API
- `mpv` — CD playback with read-ahead buffer (system package)
- `cd-info` — CD-Text fallback (system package, optional)
