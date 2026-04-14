# Claude Code OTEL stack

Local OSS observability stack for Claude Code's built-in OpenTelemetry exporter.
Spins up:

- **OTEL Collector** — receives OTLP from Claude Code on `:4317` (gRPC) / `:4318` (HTTP)
- **Prometheus** — stores metrics (scrapes the collector)
- **Loki** — stores logs/events
- **Grafana** — auto-provisioned dashboard + alert rules + Slack contact point

## Setup

1. Create a Slack incoming webhook: https://api.slack.com/messaging/webhooks
2. `cp .env.example .env` and paste the webhook URL.
3. `docker compose up -d`
4. Open Grafana: http://localhost:3000 (anonymous Admin, or `admin`/`admin`).
   The "Claude Code — Cost & Usage" dashboard is in the **Claude Code** folder.

## Point Claude Code at the stack

Add to your shell profile (or a project `.envrc`):

```sh
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
# Optional: how often Claude Code flushes metrics (default 60000ms)
export OTEL_METRIC_EXPORT_INTERVAL=10000
export OTEL_LOGS_EXPORT_INTERVAL=5000
```

Restart any running `claude` sessions. After ~30s metrics will start showing
on the dashboard.

## Point GitHub Copilot (VS Code) at the stack

Copilot Chat in VS Code can also push OTLP straight into the same collector.
Either set env vars before launching VS Code:

```sh
export COPILOT_OTEL_ENABLED=true
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
# Optional: also send prompt/response content (off by default)
# export COPILOT_OTEL_CAPTURE_CONTENT=true
```

…or configure in VS Code settings:

```json
{
  "github.copilot.chat.otel.enabled": true,
  "github.copilot.chat.otel.otlpEndpoint": "http://localhost:4317",
  "github.copilot.chat.otel.exporterType": "otlp-grpc"
}
```

Reload the window. The "GitHub Copilot — Usage & Latency" dashboard lives in
the **Copilot** folder.

## Point Cursor at the stack

Cursor telemetry comes from two sources:

### 1. Local activity metrics (all plans — automatic)

The `cursor-shipper` container reads Cursor's local SQLite databases and pushes
metrics every 30 s. It requires no configuration beyond having Cursor installed
and logged in.

**What you get:**
- `cursor_local_requests` — unique agent request IDs (proxy for AI requests)
- `cursor_local_code_files{file_ext}` — AI-generated code files by extension
- `cursor_local_lines_added` — lines of code added by Cursor agent

The shipper mounts two read-only host paths:

| Host path | What it reads |
|---|---|
| `~/Library/Application Support/Cursor/User/globalStorage/` | Auth token, composer sessions |
| `~/.cursor/ai-tracking/` | Agent requests, code hashes, scored commits |

> **macOS only.** If you're on Linux, update the volume mounts in
> `docker-compose.yml` to point at `~/.config/Cursor/` instead.

### 2. Premium fast-request counters (Pro/Business plans)

The shipper also polls `https://api2.cursor.sh/auth/usage` and pushes
`cursor_usage_requests` / `cursor_usage_tokens` gauges (monthly running totals,
reset each billing cycle). On the **Free plan these will always be 0** — Cursor
only counts premium fast requests there.

### 3. Agent turn traces (optional — all plans)

Install [cursor-otel](https://github.com/smith/cursor-otel) as a Cursor MCP
server to get per-turn traces: latency (p50/p95), model, tool calls, and
conversation logs.

```bash
git clone https://github.com/smith/cursor-otel.git ~/cursor-otel
cd ~/cursor-otel && npm install
```

Add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "cursor-otel": {
      "command": "node",
      "args": ["/Users/YOUR_USER/cursor-otel/index.mjs"]
    }
  }
}
```

Then add a Cursor rule (`.cursor/rules/otel.mdc` in any project) instructing
the agent to call `start_turn` / `end_turn` on every turn:

```
At the start of every agent turn call the cursor-otel start_turn tool with the user message.
At the end of every agent turn call the cursor-otel end_turn tool with a brief response summary and tool_count.
```

The collector already accepts OTLP on `:4318` — no further config needed.

> **Tip:** run `/setup-cursor` in Claude Code to walk through all of this
> automatically.

## What you get

**Dashboard panels** (filterable by user + model):
- Total cost (USD) for selected range
- Sessions, lines added, total tokens
- Cost rate over time, by model
- Token rate by type (input/output/cache read/cache create)
- Top 10 users by cost
- Edit-tool accept/reject pie
- Live event log (Loki)

**Alerts → Slack** (`grafana/provisioning/alerting/alert-rules.yml`):
- Hourly cost > $5 (warning)
- Daily cost > $50 (critical)
- Token burn > 1M / 5min (warning)

Tweak thresholds in that file and `docker compose restart grafana`.

## Ports

| Service        | Port  |
|----------------|-------|
| OTLP gRPC      | 4317  |
| OTLP HTTP      | 4318  |
| Prometheus UI  | 9090  |
| Loki           | 3100  |
| Grafana        | 3000  |

## Troubleshooting

- **No data?** `docker compose logs otel-collector` — you should see batches
  arriving. Confirm `OTEL_EXPORTER_OTLP_ENDPOINT` points at `localhost:4317`
  and that `CLAUDE_CODE_ENABLE_TELEMETRY=1` is exported in the same shell where
  you run `claude`.
- **Metric names wrong?** Prometheus replaces `.` with `_` and appends `_total`
  to counters. Check raw metric names at http://localhost:8889/metrics.
- **Slack alerts silent?** Grafana → Alerting → Contact points → "slack" → Test.
  Verify `SLACK_WEBHOOK_URL` is set in `.env` (not just exported in shell).
