# Plan: Separate Cache Token Panels in Claude Code Dashboard

## Context

The Claude Code dashboard (`grafana/dashboards/claude-code.json`) combines all four token types (`input`, `output`, `cacheRead`, `cacheCreation`) into a single time series panel and a single pie chart. Since `cacheRead` tokens are typically **orders of magnitude larger** than input/output tokens (cache reads accumulate heavily because they're 90% cheaper), they completely skew the Y-axis, making the input/output lines nearly invisible.

The fix is to split each combined panel into two: one showing `input + output` tokens, and a separate one showing `cacheRead + cacheCreation` (cache) tokens — with their own independent Y-axes.

---

## Scope

Only file to modify: **`grafana/dashboards/claude-code.json`**

The stat panels (IDs 10–13) are already individual cards and do not need changes.

---

## Changes

### 1. Modify panel ID 6 — "Tokens by type (rate/s)"
- **Current**: Single time series with all 4 types (`input`, `output`, `cacheRead`, `cacheCreation`)
- **New**: Only `input` and `output` types
- **Title**: "Input & Output tokens (rate/s)"
- **Query change**: Add `type=~"input|output"` filter to `claude_code_token_usage_tokens_total`
- **Remove** the green (`cacheRead`) and purple (`cacheCreation`) color overrides
- **Grid**: Keep at `{"h": 9, "w": 12, "x": 12, "y": 27}`

### 2. Add new panel ID 17 — "Cache tokens (rate/s)"
- **Type**: `timeseries`
- **Title**: "Cache tokens (rate/s)"
- **Query**: `sum by (type) (rate(claude_code_token_usage_tokens_total{user_email=~"$user", model=~"$model", type=~"cacheRead|cacheCreation"}[5m]))`
- **Color overrides**: green for `cacheRead`, purple for `cacheCreation`
- **Grid**: `{"h": 9, "w": 12, "x": 12, "y": 36}` (right of "Tokens by model")

### 3. Modify panel ID 15 — "Token distribution by type"
- **Current**: Pie chart with all 4 types
- **New**: Only `input` and `output`
- **Title**: "Input & Output distribution"
- **Query change**: Add `type=~"input|output"` filter
- **Remove** green and purple color overrides
- **Grid**: `{"h": 9, "w": 12, "x": 0, "y": 45}`

### 4. Add new panel ID 18 — "Cache token distribution"
- **Type**: `piechart`
- **Title**: "Cache token distribution"
- **Query**: `sum by (type) (increase(claude_code_token_usage_tokens_total{user_email=~"$user", model=~"$model", type=~"cacheRead|cacheCreation"}[$__range]))`
- **Color overrides**: green for `cacheRead`, purple for `cacheCreation`
- **Grid**: `{"h": 9, "w": 12, "x": 12, "y": 45}`

### 5. Shift existing panels down by 9 units (y += 9)

To make room for the new row at y=45:
- ID 7 "Cost by user (top 10)": y=45 → y=54
- ID 8 "Edit tool decisions": y=45 → y=54
- ID 9 "Claude Code events (Loki)": y=54 → y=63

---

## Final Grid Layout

| y  | Left (x=0, w=12)               | Right (x=12, w=12)                      |
|----|--------------------------------|-----------------------------------------|
| 0  | Top-level stat row (4 panels)  |                                         |
| 5  | Token stat row (4 panels)      |                                         |
| 9  | Token Types Guide (w=24)       |                                         |
| 27 | Cost over time                 | **Input & Output tokens (rate/s)** (modified) |
| 36 | Tokens by model                | **Cache tokens (rate/s)** (NEW)         |
| 45 | **Input & Output distribution** (modified pie) | **Cache token distribution** (NEW pie) |
| 54 | Cost by user                   | Edit tool decisions                     |
| 63 | Claude Code events (Loki, w=24)|                                         |

---

## Verification

1. Open Grafana at `http://localhost:3000`
2. Navigate to "Claude Code — Cost & Usage" dashboard
3. Verify "Input & Output tokens (rate/s)" shows only blue (input) and orange (output) lines with a readable Y-axis
4. Verify new "Cache tokens (rate/s)" shows only green (cacheRead) and purple (cacheCreation) lines with its own Y-axis scaled to cache values
5. Verify pie charts are similarly split: one for input/output, one for cache tokens
6. Confirm no other panels shifted or broke
