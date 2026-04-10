#!/usr/bin/env python3
"""Polls MCPProxy activity API and ships events to Loki.
Uses SQLite for dedup so events are never pushed twice, even across restarts.
"""

import json
import os
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone

LOKI_URL = os.environ.get("LOKI_URL", "http://loki:3100/loki/api/v1/push")
MCPPROXY_URL = os.environ.get("MCPPROXY_URL", "http://host.docker.internal:18420")
MCPPROXY_API_KEY = os.environ["MCPPROXY_API_KEY"]
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "5"))
DB_PATH = os.environ.get("DB_PATH", "/data/shipper.db")

LOKI_BASE = LOKI_URL.replace("/loki/api/v1/push", "")


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}", flush=True)


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute(
        "CREATE TABLE IF NOT EXISTS shipped_events (id TEXT PRIMARY KEY, shipped_at TEXT)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_shipped_at ON shipped_events(shipped_at)"
    )
    db.commit()
    return db


def is_shipped(db, event_id):
    row = db.execute("SELECT 1 FROM shipped_events WHERE id = ?", (event_id,)).fetchone()
    return row is not None


def mark_shipped(db, event_ids):
    now = datetime.now(timezone.utc).isoformat()
    db.executemany(
        "INSERT OR IGNORE INTO shipped_events (id, shipped_at) VALUES (?, ?)",
        [(eid, now) for eid in event_ids],
    )
    db.commit()


def cleanup_old_events(db, max_age_days=30):
    """Remove dedup records older than max_age_days to keep the DB small."""
    cutoff = datetime.now(timezone.utc).timestamp() - (max_age_days * 86400)
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    db.execute("DELETE FROM shipped_events WHERE shipped_at < ?", (cutoff_iso,))
    db.commit()


def api_get(path):
    url = f"{MCPPROXY_URL}{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {MCPPROXY_API_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def loki_push(streams):
    payload = json.dumps({"streams": streams}).encode()
    req = urllib.request.Request(
        LOKI_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
        return True
    except Exception as e:
        log(f"Failed to push to Loki: {e}")
        return False


def wait_for_loki():
    while True:
        try:
            with urllib.request.urlopen(f"{LOKI_BASE}/ready", timeout=5):
                return
        except Exception:
            log("Waiting for Loki...")
            time.sleep(2)


def process_activities(db, activities):
    streams = []
    shipped_ids = []

    for rec in activities:
        event_id = rec.get("id", "")
        if not event_id or is_shipped(db, event_id):
            continue

        ts = rec.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            ts_ns = str(int(dt.timestamp() * 1e9))
        except Exception:
            ts_ns = str(int(time.time() * 1e9))

        slim = {k: v for k, v in rec.items() if k not in ("arguments", "response")}
        slim["has_sensitive_data"] = rec.get("has_sensitive_data", False)

        streams.append(
            {
                "stream": {
                    "job": "mcpproxy",
                    "source": "mcpproxy-activity",
                    "server": rec.get("server_name", "unknown"),
                    "event_type": rec.get("type", "unknown"),
                    "event_status": rec.get("status", "unknown"),
                },
                "values": [[ts_ns, json.dumps(slim)]],
            }
        )
        shipped_ids.append(event_id)

    return streams, shipped_ids


def main():
    log("Starting mcpproxy-shipper")
    log(f"  mcpproxy: {MCPPROXY_URL}")
    log(f"  loki:     {LOKI_URL}")
    log(f"  db:       {DB_PATH}")
    log(f"  interval: {POLL_INTERVAL}s")

    wait_for_loki()
    log("Loki is ready")

    db = init_db()
    poll_count = 0

    while True:
        data = api_get("/api/v1/activity?limit=100")
        if not data:
            time.sleep(POLL_INTERVAL)
            continue

        activities = data.get("data", {}).get("activities", [])
        if not activities:
            time.sleep(POLL_INTERVAL)
            continue

        streams, shipped_ids = process_activities(db, activities)

        if streams:
            if loki_push(streams):
                mark_shipped(db, shipped_ids)
                log(f"Shipped {len(streams)} events")
            # If push fails, don't mark — they'll be retried next cycle

        # Periodic cleanup
        poll_count += 1
        if poll_count % 720 == 0:  # ~every hour at 5s interval
            cleanup_old_events(db)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
