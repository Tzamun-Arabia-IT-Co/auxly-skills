---
name: auxly-execute
description: >
  Execute an accepted implementation plan (a final-plan.md from /auxly-llm-council, or any plan
  markdown/JSON the user points at). Claude is the single executor: it turns the plan's phases and
  tasks into a native todo list, works them one at a time, shows progress live through Claude Code's
  own todo UI, and writes a short PROGRESS.md you can read or share. Any decision, missing value, or
  risky/irreversible step is surfaced as a clear question in chat before proceeding — never silently.
  Deliberately simple: no web server, no dashboard, no background daemons. Use when the user accepts
  a plan and says "execute", "run the plan", "build this", or "implement final-plan.md".
---

# Auxly Execute

Execute an accepted plan. **Claude is the only executor** — it does the work with its own tools and
shows progress through Claude Code's **native todo list** (the stable, built-in live view). No console
server, no SSE, no separate dashboard to babysit. That machinery was removed on purpose: it was the
main source of "looks frozen / nothing happens" confusion. The honest model is simpler — you, the
agent, do each step and keep the user informed in chat.

## Workflow

### 1. Load the plan
Take the plan the user points at — typically `./auxly-council/runs/<ts>/final-plan.md` from
`/auxly-llm-council`, or any plan markdown / `plan.json`. Read it fully and restate the goal in one or
two lines so the user can confirm you understood it before you start.

### 2. Turn the plan into a todo list
Use **TodoWrite** to create one todo per phase/task in the plan (keep the plan's own ordering and
dependencies). This *is* the live progress view — the user watches it update in Claude Code. Mark
exactly one item `in_progress` at a time; mark it `completed` the moment it's done, before starting the
next. Don't batch completions at the end — that's what makes progress look stalled.

### 3. Work it, step by step
For each task: do the real work with your tools, run the relevant build/test/lint for that step, and
report a one-line result before moving on. Keep momentum — the user can see the todo list move and
read your short notes; you don't need a separate UI to prove work is happening.

### 4. Surface every decision in chat — don't stall silently
When you hit something that needs the user (a missing secret/value, a go/no-go on an irreversible or
production step, a real choice between options), **ask a clear, specific question in chat and wait**.
State what you need and why, and what you'll do with each answer. For risky/irreversible steps
(deploys, schema changes, deletions, cutovers, anything touching production), follow the safety rules:
do not run them yourself — give the user the exact command and let them run it, or get explicit
per-step approval first. Never guess on something irreversible.

### 5. Keep a simple progress record
Write a short `PROGRESS.md` next to the plan (or in the run folder) and keep it current: a checklist of
phases/tasks with ✓/▶/○, key results (tests passed, files changed), and any open question. It's a plain
file — readable, shareable, no server. Update it as you go.

### 6. Announce completion
When the plan is done, post a short **completion report in chat**: what shipped (phases/tasks done),
test/check results, any decisions made and how, anything deferred, and the paths touched. Mark all
todos completed. Don't end silently — the user expects an explicit "done" with a summary.

## Principles
- **One executor, one truth.** You do the work; the todo list + PROGRESS.md + chat are the only status
  surfaces. No background processes, no polling, no dashboard state to drift.
- **Stable over fancy.** Native todos beat a custom live dashboard: they can't get stuck "reconnecting"
  and never look idle while you're actually working.
- **Honest about pauses.** If you're waiting on the user, say so in chat plainly. If a step is long,
  say "this will take a while" rather than going quiet.
- **Safety first on irreversible work.** Hand production/destructive commands to the user with exact
  steps; get explicit approval per step. See the global action rules.

## Notes
- No scripts ship with this skill — it is pure instructions over Claude's native tools. That's the
  point: nothing to install, vendor, or keep in sync, so it can't fall out of date.
- Pairs with `/auxly-llm-council` (feed its `final-plan.md`) but works standalone with any plan file.
