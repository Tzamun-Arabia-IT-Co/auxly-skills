<div align="center">
  <img src="plugins/auxly/skills/auxly-llm-council/scripts/ui/assets/auxly-logo.png" width="96" alt="Auxly" />
  <h1>Auxly Skills</h1>
  <p><b>A bias-resistant multi-model planning council, and a live execution dashboard.</b><br/>
  Two Claude Code skills that work together — or on their own.</p>
</div>

---

## What's inside

| Skill | What it does |
|---|---|
| **`/auxly-llm-council`** | Convenes a council of installed CLIs (Codex, Claude, Gemini, agy/Antigravity, OpenCode, custom) to write independent implementation plans, anonymizes + randomizes them, then judges and merges into one final plan. Monitored live in a **dark, Auxly-branded** web UI that shows the council roster (agents + models + roles), renders readable Markdown, and surfaces **Risks / Pros / Cons** per plan. If you don't have codex/gemini/agy, it falls back to a **Claude-only persona council** (architect + pragmatist + risk-hawk). |
| **`/auxly-execute-dash`** | Execute an accepted plan with a **live HTML dashboard**: phase/slice progress, an **Agents & Models** panel (who's *active* vs *idle*, including subagents), **red blockers** that need human action (resolve them in the UI and execution resumes), and **amber warnings** for awareness. Takes the council's `final-plan.md` or a `plan.json`. |

Both are **pure Python standard library + a browser** — no third-party packages, no network/CDN, logo embedded. Built to run anywhere.

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
Then restart Claude Code. You'll have `/auxly-llm-council` and `/auxly-execute-dash`.

## Quick start

```text
# 1) Plan with the council
/auxly-llm-council    →  produces ./auxly-council/runs/<ts>/final-plan.md

# 2) Execute it with the live dashboard
/auxly-execute-dash   →  feed it the final-plan.md, watch progress + resolve blockers
```

Or use the dashboard standalone with any `final-plan.md` or `plan.json`.

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
│  └─ skills/
│     ├─ auxly-llm-council/            # the planning council skill
│     └─ auxly-execute-dash/           # the execution dashboard skill
├─ install.sh                          # standalone (non-plugin) installer
├─ LICENSE                             # MIT
└─ README.md
```

## License
MIT — see [LICENSE](LICENSE).
