#!/usr/bin/env python3
"""Add poll-based position tracking."""

with open("/home/noahs.guest/work/cd-scrobbler/cdscrobble.py", "r") as f:
    content = f.read()

# Replace the poller function to log errors
old_poller = """    def _poller():
        while running[0]:
            val = mpv_ipc.mpv_poll_timepos(sock_path, timeout=1)
            polled_timepos[0] = val
            # Sleep in small increments so we can stop quickly
            for _ in range(20):
                if not running[0]:
                    break
                time.sleep(0.05)"""

new_poller = """    def _poller():
        while running[0]:
            try:
                val = mpv_ipc.mpv_poll_timepos(sock_path, timeout=2)
                polled_timepos[0] = val
            except Exception:
                polled_timepos[0] = -999.0
            for _ in range(20):
                if not running[0]:
                    break
                time.sleep(0.05)"""

content = content.replace(old_poller, new_poller, 1)

# Replace the poll check section with logging version
old_check = """            # Check polled time-pos (CDDA doesn't push property-change events)
            pos_s = polled_timepos[0]
            if pos_s is not None and isinstance(pos_s, (int, float)):
                pos_s = float(pos_s)
                if (pos_s > 0 and pos_s >= total_duration * 0.99
                        and last_position is None):
                    pass  # skip early position
                else:
                    current_track = _current_track(pos_s) if pos_s > 0 else None
                    if (current_track is not None
                            and current_track != last_position):
                        if verbose:
                            try:
                                os.write(log_fd, b"[POLL-TRACK: %s last=%s pos=%.1f]\\n" % (current_track, last_position, pos_s))
                            except OSError:
                                pass
                        _on_track_change(current_track)"""

new_check = """            # Check polled time-pos (CDDA doesn't push property-change events)
            pos_s = polled_timepos[0]
            if pos_s == -999.0:
                if verbose:
                    try:
                        os.write(log_fd, b"[POLL-ERROR]\\n")
                    except OSError:
                        pass
            elif pos_s is not None and isinstance(pos_s, (int, float)):
                pos_s = float(pos_s)
                if verbose:
                    try:
                        os.write(log_fd, b"[POLL: %.3f last=%s]\\n" % (pos_s, last_position))
                    except OSError:
                        pass
                if (pos_s > 0 and pos_s >= total_duration * 0.99
                        and last_position is None):
                    pass
                else:
                    current_track = _current_track(pos_s) if pos_s > 0 else None
                    if (current_track is not None
                            and current_track != last_position):
                        if verbose:
                            try:
                                os.write(log_fd, b"[TRACK-CHANGE: %s -> %s]\\n" % (last_position, current_track))
                            except OSError:
                                pass
                        _on_track_change(current_track)"""

content = content.replace(old_check, new_check, 1)

with open("/home/noahs.guest/work/cd-scrobbler/cdscrobble.py", "w") as f:
    f.write(content)

import py_compile
py_compile.compile("/home/noahs.guest/work/cd-scrobbler/cdscrobble.py", doraise=True)
print("OK")
