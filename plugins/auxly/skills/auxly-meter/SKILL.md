---
name: auxly-meter
description: >
  Live token (and optional cost) meter for an Auxly run. Records per-agent/per-model token usage and
  shows a running total — tokens in/out, number of models, and an approximate $ cost — in the header
  of the shared Auxly Console. Use whenever the user wants to track token usage or spend across a
  council/execute/review run, asks "how many tokens", "what's this costing", or "show a usage meter".
  Cost is shown only when an optional prices.json is provided (tokens are always shown); estimates
  only. Works alongside any Auxly skill or standalone.
---

# Auxly Meter

Keep a live tally of token usage (and approximate cost) for the run, shown in the Console header.

All commands: `python3 scripts/_console/console.py <verb>`.

## Record usage
After each model/CLI call you want to account for, add its tokens:
```bash
python3 scripts/_console/console.py meter --agent lead --model opus --tokens-in 1200 --tokens-out 3400
python3 scripts/_console/console.py meter --agent codex-1 --model gpt-5.2-codex --tokens-in 800 --tokens-out 1500
```
Readings accumulate per `--agent`. The header shows `tokens in … · out …`, the model count, and
`≈ $…` when priced (see below). The Console need not exist yet — `meter` targets the live session;
if none is running, start one first (`console.py ensure --title "..."`) or let another skill open it.

## Cost (optional, estimates only)
Token counts are always shown. To also show an approximate dollar cost, drop a `prices.json` in the
run directory (`./auxly-console/runs/<ts>/prices.json`) **before** metering, mapping model → $/1M
tokens:
```json
{
  "opus":          { "in": 15, "out": 75 },
  "sonnet":        { "in": 3,  "out": 15 },
  "gpt-5.2-codex": { "in": 5,  "out": 15 }
}
```
These are *your* numbers — fill in current rates; the meter does not assume any. Unknown models are
counted in tokens only. The cost figure is a rough estimate, not a billing source of truth.

## Where it shows
The meter renders as chips in the Console header (top of every stage tab): total tokens in/out,
model count, and the cost chip when priced. It updates live over SSE like everything else.

## Notes
- Localhost-only token-guarded console; Python 3 stdlib + a browser only.
- Console engine vendored at `scripts/_console` (source: `plugins/auxly/shared/console`).
