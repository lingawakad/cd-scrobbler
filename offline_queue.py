"""Offline queue: store failed scrobbles and retry later."""

import json
import time
from pathlib import Path

from musicbrainz import CACHE_DIR

QUEUE_FILE = CACHE_DIR / "queue.jsonl"
MAX_RETRIES = 3
RETRY_DELAY = 60  # seconds between retries (not enforced, just metadata)


def load_queue() -> list[dict]:
    """Load pending scrobbles from the queue file."""
    if not QUEUE_FILE.exists():
        return []
    try:
        entries = []
        for line in QUEUE_FILE.read_text().strip().split("\n"):
            if line:
                entries.append(json.loads(line))
        return entries
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def save_queue(entries: list[dict]) -> None:
    """Save pending scrobbles to the queue file."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    QUEUE_FILE.write_text("\n".join(json.dumps(e) for e in entries) + "\n" if entries else "")


def enqueue(entry: dict) -> None:
    """Add a scrobble entry to the queue."""
    queue = load_queue()
    entry.setdefault("retries", 0)
    entry.setdefault("queued_at", time.time())
    queue.append(entry)
    save_queue(queue)


def dequeue() -> list[dict]:
    """Return all queued entries and clear the queue."""
    entries = load_queue()
    save_queue([])
    return entries


def flush_queue(submit_fn) -> tuple[int, int]:
    """Try to submit all queued entries. Returns (success_count, remaining_count)."""
    queue = load_queue()
    if not queue:
        return 0, 0

    success = 0
    remaining = []

    for entry in queue:
        retries = entry.get("retries", 0)
        if retries >= MAX_RETRIES:
            # Give up after too many retries
            continue
        try:
            submit_fn(entry)
            success += 1
        except Exception:
            entry["retries"] = retries + 1
            remaining.append(entry)

    save_queue(remaining)
    return success, len(remaining)


def queue_size() -> int:
    """Return the number of pending entries in the queue."""
    return len(load_queue())
