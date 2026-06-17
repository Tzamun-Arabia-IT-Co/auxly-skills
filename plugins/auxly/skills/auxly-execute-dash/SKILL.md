---
name: auxly-execute-dash
description: >
  Execute an accepted implementation plan while showing a live, Auxly-branded HTML dashboard of
  progress — phases, slices, and a progress bar that update in real time as Claude works through
  the plan. Blockers that need a human decision appear as RED notifications requiring action (the
  user types a resolution in the UI and execution resumes); warnings the user should know about but
  that need no action appear as AMBER, dismissable notifications. Use this whenever the user accepts
  a plan and wants to execute it with live monitoring, or asks to "run the plan with a dashboard",
  "execute and show progress", or "track execution". Pairs with /auxly-llm-council (feed it the
  council's final-plan.md) but also works standalone with any final-plan.md or plan.json.
---

# Auxly Execute Dashboard

Run an accepted plan and give the user a live window into execution: where it has reached, which
phase and slice is active, and any blockers or warnings — with blockers requiring human action
surfaced in red and resolvable directly in the UI.

**Execution model:** Claude is the executor. Claude works through the plan with its own tools and
pushes progress events to a small local dashboard server via the `dash.py` CLI. The server streams
state to the browser over SSE. Nothing runs shell commands unattended — every slice is performed by
Claude, so it stays safe and portable across machines.

## When to use
- The user accepted a plan (e.g. the `final-plan.md` from `/auxly-llm-council`) and wants to execute it.
- The user asks to run/execute a plan with a progress dashboard, or to track/monitor execution.

## Workflow

### 1. Start the dashboard
```bash
python3 scripts/dash.py start --plan /path/to/final-plan.md --title "DB migration"
```
- Accepts a council `final-plan.md` (parses `### Phase N: …` / `#### Task N.M: …`) **or** a
  `plan.json` (`{ "title": "...", "phases": [ { "id": "1", "name": "...", "slices": ["...", ...] } ] }`).
- Launches the server detached, opens the browser, prints the URL and writes a session file to
  `./auxly-dash/current-session.json` (later commands find it automatically).
- Tell the user the dashboard is open and they can watch progress there.

### 2. Execute slice by slice, emitting events
As you work the plan, keep the dashboard truthful:
```bash
python3 scripts/dash.py phase 1 --status active
python3 scripts/dash.py slice 1.1 --status running
# ...do the actual work for slice 1.1 with your tools...
python3 scripts/dash.py slice 1.1 --status done --note "migrated 59 tables"
python3 scripts/dash.py phase 1 --status done
python3 scripts/dash.py log "Phase 1 complete"
```
Valid statuses — phase: `pending|active|done|failed`; slice: `pending|running|done|failed|blocked`.

### 3. Register the agents/models working on the run
Show the user exactly which agents and models are doing the work — including any subagents you
dispatch — and tag who is active vs idle. Register each agent once, then flip its status as it
picks up and finishes work:
```bash
# the lead executor (you)
python3 scripts/dash.py agent lead --name "Claude (lead)" --kind claude --model opus --role executor --status active --current "slice 1.1"
# a dispatched subagent
python3 scripts/dash.py agent sub-tests --name "test-writer" --kind claude --model sonnet --role subagent --status active --current "slice 2.2"
# when a subagent finishes its task
python3 scripts/dash.py agent sub-tests --status idle
```
- `--status active|idle|done` drives the green (blinking) **ACTIVE** / grey **IDLE** / blue **DONE**
  tag in the dashboard's "Agents & Models" panel.
- `--current` shows what each agent is working on right now (e.g. `slice 1.2`). Setting status to
  `idle`/`done` clears it.
- `--kind` picks the icon (codex ◆ · claude ✦ · gemini ✧ · agy ▲). Register subagents the moment
  you dispatch them so the user can see the whole working set.

### 4. Warnings (awareness, no action)
Raise an AMBER warning when the user should know something but no decision is required:
```bash
python3 scripts/dash.py warning --subject "PG14 source vs PG16 target" \
  --detail "Version mismatch is intentional but worth noting."
```

### 5. Blockers (RED, require human action)
When you genuinely cannot proceed without a human decision/secret/approval, raise a blocker, then
wait for the user to resolve it in the UI:
```bash
python3 scripts/dash.py blocker --id db-pass --slice 1.2 \
  --subject "Database password required" \
  --detail "Need the prod Postgres password to run the restore."
# Poll until the user resolves it in the dashboard (bounded; re-invoke if it returns unresolved):
python3 scripts/dash.py wait-blocker db-pass --timeout 120
```
- `wait-blocker` prints `{"resolved": true, "resolution": "..."}` once the user clicks **Resolve &
  resume** in the UI. Use that resolution and continue. If it returns `resolved: false`, call it
  again (it is bounded so a single call never hangs forever).
- `python3 scripts/dash.py poll` drains all pending UI actions (resolutions + dismissals) if you
  prefer to handle them in a batch.
- Raising a blocker flips the run status to **blocked** (red, blinking) until every blocker is
  resolved.

### 6. Finish
```bash
python3 scripts/dash.py done        # marks the run complete (green)
# or, on unrecoverable failure:
python3 scripts/dash.py set --status failed
```
When all slices are `done`, the run auto-completes. Keep the session responsive until execution
finishes so the user can watch and resolve blockers.

## Dashboard UI
- Auxly dark theme + embedded logo (`scripts/ui/logo.js`) — self-contained, no network/CDN.
- Header: run title + id, a live run-status badge (running / blocked / complete / failed), a blinking
  connection indicator, and a progress bar (% and slices done/total).
- **Agents & Models**: every agent and subagent on the run, with its model, role, what it is working
  on, and a live ACTIVE (green, blinking) / IDLE / DONE tag.
- **Execution Plan**: every phase with its slices and live status icons
  (pending ○, running ●, done ✓, failed ✕, blocked !).
- **Notifications**: red blockers (with a resolve box + "Resolve & resume") and amber warnings
  (dismissable).
- **Activity Log**: timestamped progress lines.

## CLI reference (`scripts/dash.py`)
| Command | Purpose |
|---|---|
| `start --plan <md\|json> [--title T]` | parse plan, launch dashboard, open browser |
| `phase <id> --status <s> [--name N]` | update a phase |
| `agent <id> [--name N] [--kind K] [--model M] [--role R] [--status active\|idle\|done] [--current X]` | register/update an agent or subagent |
| `slice <id> --status <s> [--note N]` | update a slice |
| `warning --subject S [--detail D] [--id I] [--slice X]` | amber warning |
| `blocker --subject S [--detail D] [--id I] [--slice X]` | red blocker (needs action) |
| `wait-blocker <id> [--timeout N]` | block until resolved (bounded) |
| `poll` | drain UI actions (resolutions / dismissals) |
| `log <msg>` / `set --status <s>` / `done` / `state` | misc |

All subcommands target `./auxly-dash/current-session.json` by default; pass `--session <path>` to
target a specific run.

## Notes
- Localhost-only, token-guarded server. State is snapshotted to `./auxly-dash/runs/<ts>/state.json`.
- All values rendered in the UI are HTML-escaped before display.
- Requires only Python 3 (standard library) and a browser — no third-party packages.
