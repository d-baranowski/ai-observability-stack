#!/usr/bin/env python3
"""Ships Cursor AI activity metrics to the OTEL collector.

Two data sources:
  1. Cursor usage API (api2.cursor.sh) — premium fast requests & tokens by model.
     NOTE: On the Free plan this only tracks GPT-4 "fast" quota and will show 0
     while using the slow/free model tier.
  2. Local ai-tracking SQLite DB — agent requests, files touched, and
     lines of code generated. Works on all plans.

Metrics pushed:
  cursor_usage_requests{model}           — premium requests this billing month (API)
  cursor_usage_tokens{model}             — premium tokens this billing month (API)
  cursor_local_requests                  — total unique agent requestIds in local DB
  cursor_local_code_files{file_ext}      — AI-generated code files by extension
  cursor_local_lines_added               — total lines added by Cursor agent
"""

import json
import os
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone

CURSOR_DB        = os.environ.get("CURSOR_DB",        "/data/cursor-globalStorage/state.vscdb")
AI_TRACKING_DB   = os.environ.get("AI_TRACKING_DB",   "/data/cursor-ai-tracking/ai-code-tracking.db")
OTEL_ENDPOINT    = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")
POLL_INTERVAL    = int(os.environ.get("POLL_INTERVAL", "30"))
CURSOR_USAGE_URL = "https://api2.cursor.sh/auth/usage"


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}", flush=True)


def open_ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)


# ── Cursor API (premium usage) ─────────────────────────────────────────────────

def get_access_token():
    try:
        conn = open_ro(CURSOR_DB)
        row = conn.execute(
            "SELECT value FROM ItemTable WHERE key='cursorAuth/accessToken'"
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        log(f"Auth token read failed: {e}")
        return None


def fetch_api_usage(token):
    req = urllib.request.Request(
        CURSOR_USAGE_URL,
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


# ── Local ai-tracking DB ───────────────────────────────────────────────────────

def read_local_stats():
    """Return (unique_requests, files_by_ext, total_lines_added)."""
    try:
        conn = open_ro(AI_TRACKING_DB)

        # Unique agent request IDs → proxy for number of AI requests
        row = conn.execute(
            "SELECT COUNT(DISTINCT requestId) FROM ai_code_hashes WHERE requestId IS NOT NULL"
        ).fetchone()
        unique_requests = row[0] if row else 0

        # AI-generated code files by extension
        rows = conn.execute(
            "SELECT fileExtension, COUNT(*) FROM ai_code_hashes "
            "WHERE fileExtension IS NOT NULL GROUP BY fileExtension"
        ).fetchall()
        files_by_ext = {ext: cnt for ext, cnt in rows}

        conn.close()
        return unique_requests, files_by_ext
    except Exception as e:
        log(f"Local stats read failed: {e}")
        return 0, {}


def read_composer_lines():
    """Sum totalLinesAdded across all composerData sessions."""
    try:
        conn = open_ro(CURSOR_DB)
        rows = conn.execute(
            "SELECT value FROM cursorDiskKV WHERE key LIKE 'composerData:%' "
            "AND key != 'composerData:empty-state-draft'"
        ).fetchall()
        conn.close()
        total = 0
        for (v,) in rows:
            try:
                d = json.loads(v)
                total += d.get("totalLinesAdded") or 0
            except Exception:
                pass
        return total
    except Exception as e:
        log(f"Composer lines read failed: {e}")
        return 0


# ── OTLP ───────────────────────────────────────────────────────────────────────

def build_payload(api_usage, unique_requests, files_by_ext, total_lines):
    start_of_month = api_usage.pop("startOfMonth", "")
    ts = str(int(time.time() * 1e9))

    dp_api_req, dp_api_tok = [], []
    for model, data in api_usage.items():
        if not isinstance(data, dict):
            continue
        attrs = [{"key": "model", "value": {"stringValue": model}}]
        dp_api_req.append({"attributes": attrs, "asInt": data.get("numRequests") or 0, "timeUnixNano": ts})
        dp_api_tok.append({"attributes": attrs, "asInt": data.get("numTokens") or 0, "timeUnixNano": ts})

    dp_files = [
        {
            "attributes": [{"key": "file_ext", "value": {"stringValue": ext}}],
            "asInt": cnt,
            "timeUnixNano": ts,
        }
        for ext, cnt in files_by_ext.items()
    ]

    metrics = [
        {
            "name": "cursor_usage_requests",
            "description": f"Premium fast requests this billing month ({start_of_month})",
            "gauge": {"dataPoints": dp_api_req},
        },
        {
            "name": "cursor_usage_tokens",
            "description": f"Premium fast tokens this billing month ({start_of_month})",
            "gauge": {"dataPoints": dp_api_tok},
        },
        {
            "name": "cursor_local_requests",
            "description": "Unique agent requestIds in local ai-tracking DB (all plans)",
            "gauge": {"dataPoints": [{"asInt": unique_requests, "timeUnixNano": ts}]},
        },
        {
            "name": "cursor_local_lines_added",
            "description": "Total lines added by Cursor agent across all sessions",
            "gauge": {"dataPoints": [{"asInt": total_lines, "timeUnixNano": ts}]},
        },
    ]
    if dp_files:
        metrics.append({
            "name": "cursor_local_code_files",
            "description": "AI-generated code files in local DB by file extension",
            "gauge": {"dataPoints": dp_files},
        })

    return {
        "resourceMetrics": [{
            "resource": {"attributes": [
                {"key": "service.name", "value": {"stringValue": "cursor-shipper"}}
            ]},
            "scopeMetrics": [{"scope": {"name": "cursor-shipper"}, "metrics": metrics}],
        }]
    }


def push_otlp(payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{OTEL_ENDPOINT}/v1/metrics",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    log("Starting cursor-shipper")
    log(f"  cursor db:     {CURSOR_DB}")
    log(f"  ai-tracking:   {AI_TRACKING_DB}")
    log(f"  otel:          {OTEL_ENDPOINT}")
    log(f"  interval:      {POLL_INTERVAL}s")

    while True:
        try:
            token = get_access_token()
            api_usage = {}
            if token:
                api_usage = fetch_api_usage(token)
            else:
                log("No auth token — skipping API usage")

            unique_req, files_by_ext = read_local_stats()
            total_lines = read_composer_lines()

            payload = build_payload(api_usage, unique_req, files_by_ext, total_lines)
            push_otlp(payload)
            log(f"Shipped — local requests: {unique_req}, files: {sum(files_by_ext.values())}, lines: {total_lines}")
        except Exception as e:
            log(f"Error: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
