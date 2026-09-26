"""
Store-and-forward sync for spotty connectivity.

The agent never needs a network. Finished visits and referrals are written to
the local `outbox` table; this background worker pushes them to the district
server (SAHAYAK_SYNC_URL) whenever a connection is available.

  * Idempotent: each record carries an idempotency key, so a re-send after a
    timeout cannot create duplicates upstream.
  * Exponential back-off per record on failure (5 s -> 300 s cap).
  * Connectivity is a switch (`set_online`) so the demo can simulate a village
    with no signal. With no SAHAYAK_SYNC_URL configured, "online" means a
    simulated server that accepts every record.
"""

import json
import os
import threading
import time
import urllib.request

from . import config, store
from .log import get_logger

log = get_logger("sync")

SYNC_URL = os.environ.get("SAHAYAK_SYNC_URL")
_online = False
_wake = threading.Event()


def set_online(value: bool) -> None:
    """Flip simulated connectivity; going online triggers an immediate sync."""
    global _online
    _online = value
    log.info("connectivity -> %s", "online" if value else "offline")
    if value:
        store.execute("UPDATE outbox SET next_try_at=0 WHERE synced_at IS NULL")
        kick()


def is_online() -> bool:
    """Current (simulated) connectivity."""
    return _online


def kick() -> None:
    """Wake the sync worker now instead of waiting for its next tick."""
    _wake.set()


def _push(item: dict) -> None:
    """Send one outbox record upstream (or to the simulated server)."""
    if not SYNC_URL:
        return  # simulated district server: accept
    req = urllib.request.Request(
        SYNC_URL, data=json.dumps({"kind": item["kind"], "payload": item["payload"]}).encode(),
        headers={"Content-Type": "application/json", "Idempotency-Key": item["idem_key"]},
    )
    urllib.request.urlopen(req, timeout=10).read()


def sync_once() -> int:
    """Try every due outbox record once; returns how many were synced."""
    if not _online:
        return 0
    now = time.time()
    due = store.query("SELECT * FROM outbox WHERE synced_at IS NULL AND next_try_at <= ? ORDER BY id", (now,))
    sent = 0
    for item in due:
        try:
            _push(item)
            store.execute("UPDATE outbox SET synced_at=?, last_error=NULL WHERE id=?", (time.time(), item["id"]))
            sent += 1
        except OSError as err:
            backoff = min(config.SYNC_MAX_BACKOFF_S, config.SYNC_BASE_BACKOFF_S * 2 ** item["attempts"])
            store.execute("UPDATE outbox SET attempts=attempts+1, next_try_at=?, last_error=? WHERE id=?",
                          (now + backoff, str(err), item["id"]))
            log.warning("sync failed for %s, retry in %ss: %s", item["idem_key"], backoff, err)
    if sent:
        log.info("synced %d record(s)", sent)
    return sent


def stats() -> dict:
    """Outbox counts for the UI."""
    row = store.one("SELECT COUNT(*) FILTER (WHERE synced_at IS NULL) AS pending, "
                    "COUNT(*) FILTER (WHERE synced_at IS NOT NULL) AS synced FROM outbox")
    return {"online": _online, "pending": row["pending"], "synced": row["synced"], "server": SYNC_URL or "simulated"}


def start_worker() -> None:
    """Run the sync loop forever in a daemon thread."""
    def loop():
        while True:
            try:
                sync_once()
            except Exception:
                log.exception("sync worker error")
            _wake.wait(3)
            _wake.clear()
    threading.Thread(target=loop, daemon=True).start()
