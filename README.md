<div align="center">
  <img src="assets/auxly-banner.png" width="460" alt="Auxly — by Tzamun" />
  <h1>Auxly Skills</h1>
  <p><b>Plan → execute → verify → review → recap — in one shared, live, dark Console.</b><br/>
  Six Claude Code skills that chain together — or each work on their own.</p>

  <p>
    <img alt="Python" src="https://img.shields.io/badge/python-3.8%2B-4f93e6?style=flat-square" />
    <img alt="deps" src="https://img.shields.io/badge/dependencies-stdlib%20only-2dd4a7?style=flat-square" />
    <img alt="license" src="https://img.shields.io/badge/license-MIT-8b6fd0?style=flat-square" />
    <img alt="Claude Code" src="https://img.shields.io/badge/Claude%20Code-plugin-29c2cf?style=flat-square" />
  </p>
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

### Option A — Claude Code plugin marketplace (recommended for Claude Code)
```text
/plugin marketplace add Tzamun-Arabia-IT-Co/auxly-skills
/plugin install auxly@auxly
```
**What this does:** `marketplace add` registers this repo as a plugin source (it reads
`.claude-plugin/marketplace.json`). `install auxly@auxly` installs the **`auxly`** plugin from it,
which bundles all six skills under `plugins/auxly/skills/`. They appear immediately as
`/auxly-llm-council`, `/auxly-execute`, … — update later with `/plugin update auxly@auxly`. No clone,
no PATH changes.

### Option B — install into ALL your agent CLIs (one command)
The same skills also run in other agent tools. Clone and run the installer:
```bash
git clone https://github.com/Tzamun-Arabia-IT-Co/auxly-skills.git ~/auxly-skills
cd ~/auxly-skills
./install.sh                 # detect every supported tool and wire it up
./install.sh --dry-run       # preview first (changes nothing)
./install.sh --claude-only   # just Claude Code
./install.sh --uninstall     # cleanly remove everything it added
```

**Supported tools & how they integrate** (auto-detected — only installed ones are touched):

| Tool | Integration | How you invoke |
|---|---|---|
| **Claude Code** | native skill → `~/.claude/skills` | `/auxly-llm-council`, `/auxly-execute`, … |
| **OpenCode** | native skill → `~/.config/opencode/skills` | `/auxly-…` |
| **Qwen Code** | native skill → `~/.qwen/skills` | `/auxly-…` |
| **Kimi** | native skill → `~/.kimi/skills` | `/auxly-…` |
| **Codex** | adapter block → `~/.codex/AGENTS.md` | plain language: "run the auxly council" |
| **Gemini CLI** | adapter block → `~/.gemini/GEMINI.md` | plain language |
| **Antigravity (agy)** | adapter block → `~/.antigravity/AGENTS.md` | plain language |
| **Cursor** | adapter block → `~/.cursor/AGENTS.md` | plain language |

*Skills* (SKILL.md) are a Claude-Code-style format that OpenCode/Qwen/Kimi also read, so those four
get native `/auxly-…` commands. The others read a global instructions file, so the installer injects a
small, delimited, reversible **Auxly block** there that points at the shared Python CLI (see
[`AGENTS.md`](AGENTS.md)) — drive them by just asking. The engine is identical everywhere
(Python 3 stdlib + a browser); nothing is duplicated per tool except a symlink.

Restart a tool after installing so it rescans. Re-run `./install.sh` any time to update.

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

## Usage

You normally just **ask** — e.g. "plan this change with the council", "execute the plan with the
dashboard", "review my diff". The skill triggers, opens the Console in your browser, and reports back
in chat. The Console is one tab with stage tabs (Plan ▸ Execute ▸ Verify ▸ Review ▸ Runs ▸ Summary).

- **Plan** — `/auxly-llm-council`: answer a few intake questions; it runs the council and writes
  `final-plan.md`. Have `codex` / `gemini` / `agy` installed for a multi-vendor council; otherwise it
  uses a Claude-only persona council automatically.
- **Execute** — `/auxly-execute`: point it at the `final-plan.md`. Watch phases/slices tick, agents go
  active/idle, and checks pass/fail. If it hits a **🔴 blocker**, type your answer in the red card and
  click **Resolve & resume** — execution continues. **🟡 warnings** are FYI; dismiss them.
- **Review** — `/auxly-review`: reviews the diff from several angles; skeptics refute weak findings;
  survivors show with severity + `file:line` + verdict.
- **Meter / Digest / Board** — `/auxly-meter` (token & ≈cost in the header), `/auxly-digest` (a
  Markdown recap + `digest.md`), `/auxly-board` (a grid of every run).

**Power users — drive it directly from any tool:**
```bash
CON=~/auxly-skills/plugins/auxly/shared/console/console.py
python3 "$CON" start --plan ./final-plan.md --title "My change"   # opens the Console
python3 "$CON" slice 1.1 --status done --note "did it"
python3 "$CON" blocker --id k --subject "need a secret"
python3 "$CON" --help                                             # every verb
```

Runs are saved under `./auxly-console/runs/<timestamp>/` in the working directory.

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
