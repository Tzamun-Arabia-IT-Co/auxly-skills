---
name: auxly-board
description: >
  Home dashboard for all your Auxly runs. Scans the working directory for past and present runs
  (council plans, execute/console runs) and shows them as a grid of cards — title, kind, status, and
  stages — in a Board tab of the shared Auxly Console. Use when the user wants an overview across
  runs: "show all my runs", "open the board", "what runs do I have", "the Auxly home/dashboard".
  Spans many runs (unlike the single-run execute/review views). Standalone; needs only Python + a browser.
---

# Auxly Board

A single grid of every Auxly run found under the current directory — the home view across runs.

## Show the board
```bash
python3 scripts/_console/console.py board
# scan a different root:
python3 scripts/_console/console.py board --root /path/to/project
```
This scans for runs and opens (or attaches to) the console with a **Board** tab:
- `auxly-console/runs/*/state.json` — multi-stage console runs (execute/review/etc.)
- `auxly-dash/runs/*/state.json` — legacy execute runs
- `auxly-council/runs/*/final-plan.md` and `llm-council/runs/*/final-plan.md` — planning runs

Each card shows the run's **title**, **kind** (console / council), **status**, run id, and the
**stages** it contains. Most-recently-updated first.

## Typical use
- Start of a session: `console.py board` to see what's in flight and what's done.
- After several council/execute/review runs: the board ties them together in one tab without opening
  a tab per run.

## Notes
- Read-only: the board summarizes each run's saved `state.json` / plan; it does not modify runs.
- If a run's console server is still live, open its own URL (printed when it started) to interact;
  the board is the cross-run index.
- Localhost-only token-guarded console; Python 3 stdlib + a browser only.
- Console engine vendored at `scripts/_console` (source: `plugins/auxly/shared/console`).
