---
name: auxly-review
description: >
  Adversarial code review council. Reviews a git diff (or specified files) from multiple independent
  perspectives (correctness, security, performance, tests), then has skeptic passes try to REFUTE
  each candidate finding so only verified issues survive — killing plausible-but-wrong nitpicks.
  Confirmed findings stream into the Review tab of the shared Auxly Console with severity, file:line,
  detail, and a confirmed/rejected/unsure verdict. Use whenever the user asks to "review my code",
  "review this diff/PR", "audit these changes", or after /auxly-execute finishes building. Reviews
  written CODE — distinct from /auxly-llm-council, which judges plans before any code exists. Works
  standalone or as the Verify→Review step in the Auxly flow.
---

# Auxly Review

Find real defects in actual code, not paper plans. Multiple reviewers surface candidate issues;
adversarial verifiers try to refute each one; survivors render as findings in the **Review** tab of
the shared Auxly Console. Distinct from the planning council: this reads the **diff**.

All commands: `python3 scripts/_console/console.py <verb>`.

## Workflow

### 1. Open the Review tab (attach-or-start the console)
```bash
python3 scripts/_console/console.py ensure --title "Code review"
python3 scripts/_console/console.py stage review --kind review --title Review --status active --activate
```
If a console from /auxly-execute is already live, `ensure` attaches to it (same tab).

### 2. Get the changes to review
Default to the working diff; honor any range/files the user gives:
```bash
git diff HEAD                 # or: git diff <base>..<head> , or specific files
```
If there is no diff, ask the user what to review.

### 3. Review from independent perspectives
Spawn a reviewer per dimension — **correctness, security, performance, tests/edge-cases** — using
your subagent capability (e.g. the Task/Agent tool) or available review CLIs. Register each so the
user sees who is working:
```bash
python3 scripts/_console/console.py agent rev-sec --name "security reviewer" --kind claude --model opus --role reviewer --status active --current "scanning diff"
```
Each reviewer returns candidate findings: {severity, file, line, title, detail}.

### 4. Adversarially verify (the part that matters)
For each candidate, run an independent skeptic pass that tries to **refute** it (prompt it to default
to "rejected" unless it can prove the issue is real and reachable). Keep a finding only if it
survives. This is what stops false positives. For higher-stakes reviews, use 3 skeptics and require a
majority to confirm.

### 5. Stream findings to the Review tab
Push every verified (and notable rejected) finding:
```bash
python3 scripts/_console/console.py finding --severity high --file app/db.py --line 42 \
  --title "SQL injection in query builder" \
  --detail "User input is concatenated into the query; use parameterized queries." \
  --verdict confirmed
python3 scripts/_console/console.py finding --severity low --file util.py --line 10 \
  --title "Possible nit" --detail "Looked risky but is guarded upstream." --verdict rejected
```
Severities: `critical|high|medium|low|info`. Verdicts: `confirmed|rejected|unsure`.

### 6. Summarize + finish
```bash
python3 scripts/_console/console.py agent rev-sec --status done
python3 scripts/_console/console.py stage review --status done
python3 scripts/_console/console.py log "Review complete: 2 confirmed, 3 rejected"
```
Then report the confirmed findings to the user in chat. If the user clicks **Re-run review ▶** in the
UI you'll see a `start_review` intent from `console.py poll` — run another pass.

## Notes
- The Review tab shows each finding: severity chip, title, `file:line`, detail, and verdict
  (confirmed = red, rejected = green, unsure = amber).
- Scale effort to the ask: a quick "look over this" = 1 reviewer + single-vote verify; "thoroughly
  audit" = 4 reviewers + 3-vote adversarial verify.
- Localhost-only token-guarded console; values HTML-escaped. Python 3 stdlib + browser only.
- Console engine vendored at `scripts/_console` (source: `plugins/auxly/shared/console`).
