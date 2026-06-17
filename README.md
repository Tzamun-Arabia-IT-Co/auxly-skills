<div align="center">
  <img src="plugins/auxly/skills/auxly-llm-council/scripts/ui/assets/auxly-logo.png" width="96" alt="Auxly" />
  <h1>Auxly Skills</h1>
  <p><b>Plan → execute → verify → review → recap — in one shared, live, dark Console.</b><br/>
  Six Claude Code skills that chain together — or each work on their own.</p>
</div>

---

## What's inside

Six standalone skills that chain into one workflow — **plan → execute → verify → review → recap** —
all rendering into **one shared, dark, Auxly-branded Console** (one server, one browser tab, stage
tabs; no new tab per tool). Each skill also works on its own.

| Skill | Stage | What it does |
|---|---|---|
| **`/auxly-llm-council`** | Plan | Council of installed CLIs (Codex, Claude, Gemini, agy/Antigravity, OpenCode, custom) writes independent plans, anonymizes + randomizes, judges and merges into one final plan. Dark UI with the council roster, readable Markdown, **Risks / Pros / Cons** per plan. No codex/gemini/agy? Falls back to a **Claude-only persona council** (architect + pragmatist + risk-hawk). |
| **`/auxly-execute`** | Execute | Run an accepted plan with live phase/slice progress, an **Agents & Models** panel (active vs idle, incl. subagents), a **Checks** panel (build/test/lint), **red blockers** (resolve in-UI → resume), and **amber warnings**. Takes the council's `final-plan.md` or a `plan.json`. |
| **`/auxly-review`** | Review | Adversarial code/diff review — multiple reviewers find issues, skeptics try to **refute** each so only verified findings survive. Severity + `file:line` + verdict in the Review tab. |
| **`/auxly-meter`** | — | Live token (and optional ≈cost) meter in the Console header, per agent/model. |
| **`/auxly-digest`** | — | One-click Markdown recap of a run (shipped / blockers / checks / findings / cost) → Summary tab + `digest.md`. |
| **`/auxly-board`** | Home | Grid of **all** your runs (council + console) across the directory, in one Board tab. |

All **pure Python standard library + a browser** — no third-party packages, no network/CDN, logo embedded. Built to run anywhere.

The Console buttons (e.g. **Execute ▶**, **Review ▶**) enqueue intents the running Claude session
picks up, so one tab drives the whole flow while every skill stays independently invocable.

## Install

### Option A — Claude Code plugin marketplace (recommended)
```
/plugin marketplace add waeils/auxly-skills
/plugin install auxly@auxly
```

### Option B — standalone installer
```bash
git clone https://github.com/waeils/auxly-skills.git
cd auxly-skills
./install.sh           # symlinks into ~/.claude/skills (use --copy to copy)
```
Then restart Claude Code. You'll have all six `/auxly-*` skills.

## Quick start

```text
/auxly-llm-council   →  plan; produces ./auxly-council/runs/<ts>/final-plan.md
/auxly-execute       →  run it live (Plan + Execute tabs open in the Console)
/auxly-review        →  adversarial review of the diff (Review tab)
/auxly-digest        →  recap of the run (Summary tab + digest.md)
/auxly-board         →  see all runs (Runs tab)
```

Each is standalone — start anywhere. The Console's **Execute ▶ / Review ▶** buttons hand off to the
next skill in the same tab.

## Requirements
- Claude Code
- Python 3.8+
- A browser (the UIs are local, token-guarded, localhost-only)
- Optional planner CLIs for the council: `codex`, `gemini`, `agy`, `opencode`
  (none required — it falls back to a Claude-only council)

## Repo layout
```
auxly-skills/
├─ .claude-plugin/marketplace.json     # plugin marketplace manifest
├─ plugins/auxly/
│  ├─ .claude-plugin/plugin.json
│  ├─ shared/console/                  # single-source multi-stage Console engine
│  └─ skills/
│     ├─ auxly-llm-council/            # plan — multi-model council
│     ├─ auxly-execute/                # execute — live run dashboard
│     ├─ auxly-review/                 # review — adversarial code review
│     ├─ auxly-meter/                  # token/cost meter
│     ├─ auxly-digest/                 # run recap
│     └─ auxly-board/                  # all-runs home
├─ sync-console.sh                     # vendor shared/console into each skill
├─ install.sh                          # standalone (non-plugin) installer
├─ LICENSE                             # MIT
└─ README.md
```

## License
MIT — see [LICENSE](LICENSE).
