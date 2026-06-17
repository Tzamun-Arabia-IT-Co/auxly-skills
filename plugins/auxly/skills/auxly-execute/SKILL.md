---
name: auxly-execute
description: >
  Execute an accepted implementation plan inside the live, Auxly-branded multi-stage Console —
  phases, slices, and a progress bar that update in real time as Claude works through the plan, plus
  an Agents & Models panel showing which agents/subagents are active vs idle, and a Checks panel for
  build/test/lint gates. Blockers that need a human decision appear as RED notifications requiring
  action (the user types a resolution in the UI and execution resumes); warnings appear AMBER and
  dismissable. Renders into the same shared Auxly Console tab as plan/verify/review (no new tabs).
  Use whenever the user accepts a plan and wants to execute with live monitoring, or says "run the
  plan with a dashboard", "execute and show progress", or "track execution". Pairs with
  /auxly-llm-council (feed its final-plan.md) but also works standalone with any final-plan.md or plan.json.
---

# Auxly Execute

Execute an accepted plan and give the user a live window into it: which phase/slice is active,
progress, the agents at work, build/test checks, and any blockers or warnings — all in the **shared
Auxly Console** (one server, one browser tab, stage tabs: Plan ▸ Execute ▸ Verify ▸ Review).

**Execution model:** Claude is the single executor/orchestrator. Claude works the plan with its own
tools and pushes progress events to the console via `console.py`. Nothing runs shell commands
unattended. The **Agents & Models** rows (Engineer / Reviewer / Tester + their models) are *assignment
labels* the orchestrator reflects status onto — they are **not** separate CLIs that get spawned or
"notified" to take turns. There is no automatic role→role hand-off (e.g. codex implements → claude is
notified to review → gemini tests); the orchestrator does each part itself and updates the matching
row. If review surfaces findings, the orchestrator fixes them — it does not dispatch them back to a
per-role CLI. (Real per-role CLI dispatch is a deliberate non-goal of this skill.)

All commands use the vendored console CLI: `python3 scripts/_console/console.py <verb>`.

## Workflow

### 0. Pick up the council handoff (if present)
When the user reached you by pressing **▶ Execute** in `/auxly-llm-council`, the council run folder
(`./auxly-council/runs/<ts>/`) contains:
- `final-plan-accepted.md` — the plan to execute (use this as `--plan`).
- `execution-config.json` — `{ "crew": [{role, agent, model}], "workload": {...} }`. **Read it** and
  assign each phase/slice's work to the agent + model the user chose for that role (e.g. Engineer →
  codex, Reviewer → claude). Register them with `console.py agent <name> --model <m> --status idle/active`
  so the Agents & Models panel reflects the chosen crew. Honor the user's picks — they edited this on
  purpose before executing.
- `EXECUTE-REQUESTED` — a one-line marker pointing at the accepted plan.

If there is no handoff (standalone use), just take the plan path the user gives you.

### 1. Open the console with the plan
```bash
python3 scripts/_console/console.py start --plan /path/to/final-plan.md --title "DB migration"
```
- Parses a council `final-plan.md` (`### Phase N` / `#### Task N.M`) or a `plan.json`
  (`{ "title": "...", "phases": [ { "id":"1","name":"...","slices":["...", ...] } ] }`).
- Starts the console (if not already running — it is *attach-or-start*), opens the browser, seeds the
  **Plan** tab (the plan markdown) and an **Execute** tab (phases/slices, all pending), and writes
  `./auxly-console/current-session.json`. Tell the user the console is open.
- If a console is already live, use `ensure` instead of `start` to attach without a new tab.

### 2. Execute slice by slice
```bash
python3 scripts/_console/console.py phase 1 --status active
python3 scripts/_console/console.py slice 1.1 --status running
# ...do the real work for 1.1 with your tools...
python3 scripts/_console/console.py slice 1.1 --status done --note "migrated 59 tables"
python3 scripts/_console/console.py phase 1 --status done
python3 scripts/_console/console.py log "Phase 1 complete"
```

### 3. Show the agents/models (and subagents) at work
```bash
python3 scripts/_console/console.py agent lead --name "Claude (lead)" --kind claude --model opus --role executor --status active --current "slice 1.1"
python3 scripts/_console/console.py agent sub-tests --name "test-writer" --kind claude --model sonnet --role subagent --status active --current "slice 2.2"
python3 scripts/_console/console.py agent sub-tests --status idle   # when it finishes
```
Optionally record token usage for the live meter (see /auxly-meter):
`console.py meter --agent lead --model opus --tokens-in 1200 --tokens-out 3400`.

### 4. Checks (folded-in verify gate)
Run build/test/lint as checks shown in the Execute tab's Checks panel:
```bash
python3 scripts/_console/console.py check tests --status running --name "pytest"
python3 scripts/_console/console.py check tests --status pass --output "143 passed"
python3 scripts/_console/console.py check lint  --status fail --output "2 errors in app.py"
```
(For a dedicated, deeper verify pass use /auxly-verify; it renders into the Verify tab.)

### 5. Warnings & blockers
```bash
python3 scripts/_console/console.py warning --subject "PG14 source vs PG16 target" --detail "Intentional; just noting."
python3 scripts/_console/console.py blocker --id db-pass --slice 1.2 --subject "DB password required" --detail "Need prod Postgres password."
python3 scripts/_console/console.py wait-blocker db-pass --timeout 120
```
`wait-blocker` returns `{"resolved": true, "resolution": "..."}` when the user clicks **Resolve &
resume** in the UI. If `resolved:false`, call it again (bounded). Raising a blocker turns the run
status red/blocked until resolved.

### 6. Hand off / finish
The Execute tab shows **[Run checks ▶]** and **[Review ▶]** buttons. When the user clicks one, it
enqueues an intent — drain it and act:
```bash
python3 scripts/_console/console.py poll      # -> {"actions":[{"type":"intent","name":"start_review"}]}
```
If you see `start_review`, invoke /auxly-review; `start_verify`, run /auxly-verify. Finish with:
```bash
python3 scripts/_console/console.py done       # marks the run complete (green)
# or: console.py set --status failed
```
Keep the session responsive until execution finishes so the user can watch and resolve blockers.

## Console UI (shared)
- Auxly dark theme + embedded logo, one tab. Stage tabs across the top with live status dots.
- **Agents & Models** panel: active (green, blinking) / idle / done, with model + current slice.
- **Execute** tab: phases → slices (pending ○, running ●, done ✓, failed ✕, blocked !) + Checks panel + progress bar.
- **Notifications**: red blockers (resolve box) + amber warnings (dismiss) — visible across all tabs.
- **Meter**: tokens (and ≈cost if a prices.json is present) in the header.
- **Activity Log**: timestamped lines.

## CLI reference — `scripts/_console/console.py`
| Verb | Purpose |
|---|---|
| `start --plan <md\|json> [--title T]` / `ensure` | open (or attach to) the console |
| `phase <id> --status <s>` / `slice <id> --status <s> [--note N]` | execute progress |
| `check <id> --status pass\|fail\|running [--output O]` | build/test/lint gate |
| `agent <id> [--name --kind --model --role --status --current]` | agent/subagent roster |
| `meter --agent A --model M --tokens-in N --tokens-out N` | token meter |
| `warning` / `blocker` / `wait-blocker <id>` / `poll` | notifications + intents |
| `activate <stage>` / `log <msg>` / `set --status <s>` / `done` / `state` | misc |

## Notes
- Localhost-only, token-guarded console; state snapshotted to `./auxly-console/runs/<ts>/state.json`.
- All UI values are HTML-escaped before display. Python 3 stdlib + a browser only.
- The console engine is vendored at `scripts/_console` (single source in the repo at
  `plugins/auxly/shared/console`; maintainers run `sync-console.sh` to update).
