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

**The dashboard is the single control surface — work in the background, never block on a terminal
prompt.** Once execution starts, keep driving the plan without stopping the chat to ask a free-text
question. Anything that would make you pause for the user — a decision, a missing value, a risky/
irreversible step, an FYI — goes **into the dashboard**, not the terminal:
- A choice or something you need from the user (password location, go/no-go on an irreversible step,
  pick between options) → raise a **blocker** (`console.py blocker ...`) and `wait-blocker` on it. The
  user resolves it **in the UI** ("Resolve & resume") and you continue. Do **not** print "(yes / no)"
  in chat and sit there — that strands the run looking idle (the user can't see a terminal question
  from the dashboard).
- A non-blocking heads-up → raise a **warning** (`console.py warning ...`); it shows amber and the user
  dismisses it. Never silently swallow a concern — surface it.

Run the actual work so the user can keep watching live: do long/independent steps with backgrounded
shells (Bash `run_in_background`) where it helps, and **poll `status` / `wait-blocker`** to learn when
they finish or when the user has answered a blocker. The chat stays responsive; the dashboard shows
truth. The only things that ever stop you are open blockers — and those live in the UI, resolved by the
user there.

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

### 2. Execute slice by slice — push updates in real time
The dashboard only reflects what you tell it, and it streams to the browser the instant you emit an
event (SSE). So update it **as you go, not in a batch at the end** — the user is watching it move:
- Mark a slice `running` **before** you start the work, and `done` (or `failed`) **immediately after**,
  not after the whole phase. A slice that is silently worked for minutes looks stalled.
- Flip the matching **agent row** to `active` while it's working and `idle`/`done` when it finishes, and
  set `--current` to the slice it's on, so "Agents & Models" tracks reality.
- `log` short milestones; run `check`s as they complete. Each call is a live push — frequent small
  events keep progress, the % bar, and the agent dots honest. Treat "no event in a while" as a smell:
  if a step is long, emit an interim `log`/`--note` or a slice still-`running` heartbeat so the user
  knows it's alive, not frozen.
```bash
python3 scripts/_console/console.py phase 1 --status active
python3 scripts/_console/console.py agent eng --status active --current "slice 1.1"
python3 scripts/_console/console.py slice 1.1 --status running
# ...do the real work for 1.1 with your tools...
python3 scripts/_console/console.py slice 1.1 --status done --note "migrated 59 tables"
python3 scripts/_console/console.py agent eng --status idle
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

### 5. Warnings & blockers — every pause goes here, not to the terminal
This is the channel for *all* user-facing interruptions during a background run. If you catch yourself
about to type a question into chat ("should I…? (yes/no)"), stop — raise it as a blocker instead so it
appears in the dashboard the user is watching.
```bash
python3 scripts/_console/console.py warning --subject "PG14 source vs PG16 target" --detail "Intentional; just noting."
python3 scripts/_console/console.py blocker --id db-pass --slice 1.2 --subject "DB password required" --detail "Need prod Postgres password."
python3 scripts/_console/console.py wait-blocker db-pass --timeout 120
```
`wait-blocker` returns `{"resolved": true, "resolution": "..."}` when the user clicks **Resolve &
resume** in the UI. If `resolved:false`, call it again (bounded). Raising a blocker turns the run
status red/blocked until resolved — so the dashboard's "0 blockers" badge always tells the user
truthfully whether anything is waiting on them.

**Rules of thumb:**
- Needs a human decision / secret / go-ahead on something irreversible → **blocker** + `wait-blocker`
  (the user answers in the UI, you read the resolution and continue). Never strand the run on a
  terminal prompt — from the dashboard that just looks frozen/idle.
- Worth knowing but doesn't stop work → **warning** (amber, dismissable). Surface concerns; don't bury
  them in chat text the user may not be reading.
- Resolve a blocker yourself only if conditions changed (`console.py blocker --id <id> --resolve`);
  otherwise let the user clear it in the UI.

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

**Know when it's done — poll the status, don't assume.** You (the orchestrator) drive the work, so you
normally know when each slice finishes. But if any part runs in the background / async, or you stepped
away, **check status from time to time** rather than guessing:
```bash
python3 scripts/_console/console.py status   # {run_status, complete, progress, open_blockers, open_warnings}
```
Poll this periodically until `complete` is true. If the run is long or runs unattended, schedule a
recurring check so completion is detected by default — use the `/loop` skill (e.g. `/loop 2m
<re-check status>`) or a cron/`ScheduleWakeup` so you wake, run `status`, and act when it flips to
complete or an `open_blockers` entry appears (resolve it, then resume). `wait-blocker` does a bounded
poll for one specific blocker; `status` is the general "is it done / anything waiting on me?" check.

**Always announce completion — this is the orchestrator's final, required step.** When all stages are
done, you MUST: (1) call `console.py done` (or `set --status failed`) so `run_status` flips to
`complete` — the dashboard fires a completion notification (a success toast + a desktop browser
notification if the user granted permission), so they're pinged even with the tab in the background;
and (2) post a short **completion report in chat** — what shipped (phases/slices done), test/check
results, review findings (confirmed vs rejected), any blockers hit + how resolved, and the run path.
Do not end the task silently; the user expects an explicit "task complete" from the orchestrator.

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
