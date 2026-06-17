---
name: auxly-digest
description: >
  Summarize an Auxly run into a clean Markdown digest — what was planned, what shipped (phases/slices
  done vs total), blockers hit and how they were resolved, warnings raised, check results, review
  findings, and token/cost — then render it as a Summary tab in the shared Auxly Console and save it
  to digest.md. Use at the end of a run, or when the user asks to "summarize this run", "what
  happened", "write a recap/changelog", or "give me the digest". Works on the live console state or a
  saved run's state.json, standalone or after any Auxly skill.
---

# Auxly Digest

Turn a run's state into a shareable Markdown recap, shown as a **Summary** tab and written to disk.

## 1. Get the run state
- Live console running: `python3 scripts/_console/console.py state > /tmp/auxly-state.json`
- Or a saved run: read `./auxly-console/runs/<ts>/state.json` directly.

## 2. Build the digest
Read the state JSON and write `digest.md` with this structure (omit empty sections):
```markdown
# <run title> — Digest
**Run:** <run_id> · **Status:** <run_status> · **Progress:** <done>/<total> slices (<pct>%)

## What shipped
- Phase 1 <name> — done (slices 1.1, 1.2)
- ...

## Checks
- pytest — pass (143 passed)
- ruff — fail (2 errors)

## Blockers
- DB password required (slice 1.2) — resolved: "used vault key prod/pg/password"

## Warnings
- PG14 vs PG16 — noted

## Review findings
- HIGH app/db.py:42 — SQL injection in query builder (confirmed)

## Agents & cost
- lead (opus), test-writer (sonnet)
- Tokens: in <n> / out <n> · ≈ $<cost> (if priced)
```
Pull every value from the state's `stages`, `blockers`, `warnings`, `agents`, `meter`. Keep it tight
and factual — a recap someone can paste into a PR description or standup.

## 3. Render + save
```bash
python3 scripts/_console/console.py md summary --kind markdown --title "Summary" --file digest.md --activate
```
This adds (or updates) a **Summary** tab showing the digest, and switches to it. Also leave
`digest.md` on disk next to the run so the user can copy/commit it. Finally, paste the digest into
chat for the user.

## Notes
- Standalone (no live console): still produce `digest.md` from a `state.json`; the `md` render step
  just needs a running console (start one with `console.py ensure` if the user wants the tab).
- Console engine vendored at `scripts/_console` (source: `plugins/auxly/shared/console`).
