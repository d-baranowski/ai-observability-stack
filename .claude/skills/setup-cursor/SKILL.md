---
name: setup-cursor
description: Set up Cursor AI telemetry for the ai-observability stack
allowed-tools: Bash Read Write Edit Glob Grep
---

# Set Up Cursor Telemetry

Walk the user through connecting Cursor to the ai-observability stack.
The stack must already be running (`docker compose up -d`).

## Steps

### 1. Detect OS and verify Cursor is installed

Check the platform:
- macOS: `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb`
- Linux: `~/.config/Cursor/User/globalStorage/state.vscdb`

If the DB file does not exist, tell the user Cursor doesn't appear to be installed
and stop.

Store the detected DB path as `CURSOR_GLOBALDB`.
Store the Cursor data root (`~/Library/Application Support/Cursor/User/globalStorage`
or `~/.config/Cursor/User/globalStorage`) as `CURSOR_GLOBALDIR`.

### 2. Check the stack is running

Run `docker compose ps` in the repo root. If `cursor-shipper` is not listed as
running, run `docker compose up -d cursor-shipper` and wait for it to start.

### 3. Verify the docker-compose volume mounts are correct for this OS

Read `docker-compose.yml` and find the `cursor-shipper` volumes block. The two
host-side paths must match:
- `CURSOR_GLOBALDIR` for the globalStorage mount
- `~/.cursor/ai-tracking` for the ai-tracking mount (same on all platforms)

If the paths in the file don't match (e.g. the file still has a macOS path but
the user is on Linux), update them with the correct OS-specific paths and restart
the shipper: `docker compose up -d --force-recreate cursor-shipper`.

### 4. Verify the shipper is shipping

Run `docker logs cursor-shipper --tail 5`. Look for a line like:
```
Shipped — local requests: N, files: N, lines: N
```

If it shows an error reading the DB, the volume mount path is wrong — go back to
step 3.

If it shows `local requests: 0, files: 0, lines: 0`, the user hasn't run any
Cursor agent sessions yet. Tell them to start a Cursor agent session and come
back.

### 5. Check the Cursor API auth (premium request counters)

Run:
```bash
sqlite3 "$CURSOR_GLOBALDB" "SELECT value FROM ItemTable WHERE key='cursorAuth/accessToken';" 2>/dev/null | head -c 20
```

If output is empty, the user is not logged in to Cursor. Remind them to sign in
via Cursor → Settings → Account and then restart the shipper.

If a token is present, try the usage endpoint:
```bash
TOKEN=$(sqlite3 "$CURSOR_GLOBALDB" "SELECT value FROM ItemTable WHERE key='cursorAuth/accessToken';" 2>/dev/null)
curl -s -H "Authorization: Bearer $TOKEN" "https://api2.cursor.sh/auth/usage"
```

Show the response. Explain that `numRequests: 0` is normal on the Free plan
(only counts premium fast requests) and that the local-activity metrics work
regardless.

### 6. Check MCP proxy config (optional but recommended)

Check whether `~/.cursor/mcp.json` already contains an `mcpproxy` entry.

If not, and if MCPProxy is reachable at `http://127.0.0.1:18420/mcp`, offer to
add it. If the user agrees, read the existing `~/.cursor/mcp.json`, add the
entry, and write it back:

```json
{
  "mcpServers": {
    "mcpproxy": {
      "url": "http://127.0.0.1:18420/mcp"
    }
  }
}
```

Tell the user to reload Cursor (`Cmd+Shift+P` → "Developer: Reload Window").

### 7. Offer to set up cursor-otel (agent turn traces)

Ask the user if they want per-turn latency, model, and conversation log panels
(requires cursor-otel).

If yes:
1. Check if `~/cursor-otel/index.mjs` already exists. If not, run:
   ```bash
   git clone https://github.com/smith/cursor-otel.git ~/cursor-otel
   cd ~/cursor-otel && npm install
   ```
2. Read `~/.cursor/mcp.json` and add the cursor-otel server entry (without
   removing existing entries):
   ```json
   "cursor-otel": {
     "command": "node",
     "args": ["/Users/USERNAME/cursor-otel/index.mjs"]
   }
   ```
   Use the real expanded home directory path (not `~`).
3. Tell the user to:
   - Reload Cursor (`Cmd+Shift+P` → "Developer: Reload Window")
   - Add a Cursor rule in their projects instructing the agent to call
     `start_turn` / `end_turn` on every turn (see README for the exact text)

### 8. Open the dashboard

Tell the user to open:
http://localhost:3000/d/cursor-overview

And explain the three sections:
- **Local Activity** — live from your machine, all plans, updates every 30 s
- **Premium Fast Requests** (collapsed) — from Cursor API, Pro/Business only
- **Agent Turns** (collapsed) — from cursor-otel, requires step 7

## Output format

After completing each step, print a one-line status:
- `✓ Cursor DB found at <path>`
- `✓ cursor-shipper running — last log: <last line>`
- `✓ Auth token present, plan: <membershipType>`
- `✓ MCP proxy configured`
- `✓ cursor-otel installed` (if done)
- `→ Dashboard: http://localhost:3000/d/cursor-overview`

Keep each status short. Don't dump raw JSON at the user unless they ask.
