---
name: auxly-execute
description: >
  Execute an accepted implementation plan (a final-plan.md from /auxly-council, or any plan
  markdown/JSON the user points at). Claude acts as the ORCHESTRATOR: it first asks the user which
  agents/models should form the execution crew (implementer, tester, reviewer), then runs the
  implementation through background subagents, monitors them, and gates each chunk of work behind a
  tester subagent (runs the relevant tests) and a reviewer subagent (code review) before moving on.
  Progress is shown live through Claude Code's native todo list plus a short PROGRESS.md. Any decision,
  missing value, or risky/irreversible step is surfaced as a clear question in chat — never silently,
  and never delegated to a background agent. When the run finishes it writes a self-contained,
  Auxly-branded execute-report.html (implementation summary, files changed, how-to-test, git push
  status, and token usage per agent/model) and opens it in the browser. Use when the user accepts a
  plan and says "execute", "run the plan", "build this", or "implement final-plan.md".
---

# Auxly Execute

Execute an accepted plan. **Claude is the orchestrator**: it picks a crew with the user, dispatches the
implementation to **background subagents**, **monitors** them, and runs a **tester** and a **reviewer**
over each chunk before it's accepted. The user watches progress through Claude Code's **native todo
list** and a short `PROGRESS.md`. There is no web dashboard or daemon — the todo list + chat + the
subagents' returned reports are the status surface (that's deliberate: a custom live dashboard was the
old "looks frozen" trap).

## Workflow

### 1. Load the plan
Take the plan the user points at — typically `./auxly-council/runs/<ts>/final-plan.md` from
`/auxly-council`, or any plan markdown / `plan.json`. Read it fully and restate the goal in one or two
lines so the user can confirm you understood it before anything runs.

### 2. Pick the execution crew (REQUIRED first interactive step)
Before doing any work, let the user choose **who runs this execution**. Three roles:
- **Implementer** — does the code changes for each task.
- **Tester** — writes/runs the relevant tests and reports pass/fail.
- **Reviewer** — reviews the implementer's diff for bugs, security, and plan-adherence.

Offer concrete backings for each role and let the user select (use **AskUserQuestion**, one question
per role, multi-select where it makes sense):
- **Claude subagents** (always available) — a general-purpose subagent for the implementer, and
  specialized ones where they fit (e.g. a `code-reviewer` / language-specific reviewer for the reviewer
  role, a TDD/test agent for the tester). This is the default if the user has no preference.
- **Installed model CLIs** — scan the machine the same way the council does and offer any that are
  present (codex, gemini, agy/Antigravity, etc.). To list them, reuse the council's detector:
  `python3 <auxly-council skill dir>/scripts/llm_council.py detect` (the auxly-council skill lives
  next to this one — under the same `skills/` dir, or the plugin cache). Only offer CLIs that the scan
  actually finds; never assume one is installed.

Confirm the chosen crew back to the user in one line (e.g. "Implementer: Claude general-purpose ·
Tester: Claude tdd agent · Reviewer: codex") before starting. If the user just says "go", use the
all-Claude default and say so.

### 3. Turn the plan into a todo list
Use **TodoWrite** to create one todo per phase/task in the plan, preserving its ordering and
dependencies. This is the live progress view. Keep exactly one item `in_progress`, mark it `completed`
the moment its implement→test→review loop passes. Don't batch completions — that's what looks stalled.

### 4. Implement in the background, and monitor
For each task (respecting dependencies), **dispatch the implementer as a background subagent** (run it
in the background so you stay responsive and the user isn't blocked). Give the subagent: the task, the
relevant plan section, the files/paths involved, and the acceptance criteria. While it runs:
- **Monitor** it — you are re-invoked when a background subagent finishes; report a one-line result.
- Keep the **todo list** and **PROGRESS.md** current as tasks move.
- You may run independent tasks concurrently, but respect the plan's dependencies — don't start a task
  whose inputs aren't done.

**Capture each subagent's cost.** When a background subagent finishes, its task notification includes
`total_tokens` and `duration_ms`. Record them per subagent (role + agent + model) as you go — you'll
report this as token-usage-by-agent in the final HTML report, and there's no other chance to capture it.

### 5. Gate each chunk: tester + reviewer
When an implementer subagent returns a chunk of work, before marking the todo done:
- Spawn the **tester** subagent to run the build/tests/lint for that change and report pass/fail with
  details.
- Spawn the **reviewer** subagent to review the diff for correctness, security, and whether it matches
  the plan.
- If tests fail or the reviewer flags a real issue, loop back: hand the findings to the implementer for
  a fix, then re-test/re-review. Only mark the todo `completed` when tests pass and review is clean.
- Surface a short summary of each gate in chat (tests: X passed; review: clean / N issues fixed).

### 6. Surface every decision in chat — never silently, never delegated
When something needs the user (a missing secret/value, a go/no-go on an irreversible or production
step, a real choice between options), **ask a clear, specific question in chat and wait**. State what
you need, why, and what each answer leads to.
- **Never delegate a risky/irreversible action to a background subagent.** Deploys, schema changes,
  deletions, cutovers, anything touching production or money: do not run them yourself and do not let a
  subagent run them — give the user the exact command and let them run it, or get explicit per-step
  approval first. See the global action rules. Background subagents are for normal, reversible build
  work only.

### 7. Keep a simple progress record
Write a short `PROGRESS.md` next to the plan (or in the run folder) and keep it current: a checklist of
phases/tasks with ✓/▶/○, the crew used, key results (tests passed, files changed, review outcome), and
any open question. Plain file — readable, shareable, no server.

### 8. Announce completion
When the plan is done, post a short **completion report in chat**: what shipped, the crew that ran it,
test/review results, decisions made and how, anything deferred, and the paths touched. Mark all todos
completed. Don't end silently.

### 9. Write the HTML report (`execute-report.html`)
After completion, generate a branded, self-contained HTML report so the user has a shareable record.
Gather the data, then run the bundled renderer:

```bash
python3 <this skill dir>/scripts/render_report.py --spec report.json --out <run dir>
```

(or pipe the JSON on stdin). It writes `execute-report.html` next to the plan and opens it in the
browser. **Assemble the JSON yourself** from what actually happened — do not invent values:
- `title` / `goal` — the plan name and one-line goal.
- `summary_md` — a short Markdown summary of what was built.
- `phases` — `[{name,status,note}]` from your todo list (status: done / partial / blocked).
- `changes` — files touched: `[{path,status,note}]` (status: added / modified / deleted). Get this from
  `git status --porcelain` and `git diff --stat`.
- `tests` — `{commands:[...], result:"…", status:"pass"|"fail"}`: the exact commands to re-run the
  tests and the last result.
- `git` — `{branch, remote, pushed, ahead, behind, uncommitted, last_commit}`. Gather with read-only
  git: `git rev-parse --abbrev-ref HEAD`, `git status -sb`, `git rev-list --count @{u}..HEAD` (ahead)
  and `@{u}` for behind, `git log -1 --oneline`. Set `pushed:true` only if ahead==0 and a remote
  tracking branch exists; otherwise `pushed:false`. **Do not push** to set this true — just report it.
- `crew` — one row per agent that worked: `[{role,agent,model,tokens,duration_s}]` using the
  token/duration figures you captured in step 4. This is the token-usage-by-agent table.
- `next_steps` — what the user should do next (push, open PR, finish a deferred task).

The renderer is a pure presenter — it only displays what you pass and runs no git or shell itself.

## Principles
- **Orchestrator + crew.** You coordinate; subagents do the implement/test/review work. The user picks
  the crew up front and can change it any time.
- **Background, monitored.** Implementation runs in the background so the session stays responsive; you
  watch it and report, rather than blocking on each step or going quiet.
- **Every chunk is tested and reviewed** before it counts as done — the tester and reviewer are gates,
  not afterthoughts.
- **Stable over fancy.** Native todos + PROGRESS.md + chat are the only status surfaces. No background
  daemon, no custom dashboard to drift or get stuck "reconnecting".
- **Safety first on irreversible work.** Production/destructive commands go to the user with exact
  steps and explicit per-step approval — never auto-run, never delegated to a subagent.
- **Honest about pauses.** If you're waiting on a subagent or on the user, say so plainly in chat.

## Notes
- The execution itself is pure native tools (TodoWrite, background subagents, and any installed model
  CLIs the user selects) — nothing to install. The only bundled code is `scripts/render_report.py`, a
  stdlib-only presenter that renders the final `execute-report.html` from JSON you assemble; it runs no
  git or shell of its own.
- Pairs with `/auxly-council` (feed its `final-plan.md`) but works standalone with any plan file.
