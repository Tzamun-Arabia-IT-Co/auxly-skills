#!/usr/bin/env bash
# Auxly Skills — multi-tool installer.
#
# Installs the Auxly skill suite into every supported agent CLI found on the
# machine. Two integration styles, picked automatically per tool:
#
#   * Skills-native tools (Claude Code, OpenCode, Qwen, Kimi) read SKILL.md from
#     a skills/ directory  -> each skill is symlinked there.
#   * Instruction-based tools (Codex, Gemini, Antigravity/agy, Cursor) read a
#     global context file   -> a delimited Auxly block is injected pointing at
#     the shared Python CLI (see AGENTS.md).
#
# Usage:
#   ./install.sh                 # install into all detected tools
#   ./install.sh --claude-only   # only Claude Code
#   ./install.sh --copy          # copy skills instead of symlinking
#   ./install.sh --uninstall     # remove everything this installer added
#   ./install.sh --dry-run       # show what would happen
set -euo pipefail

HOME_DIR="${HOME}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_SRC="$ROOT/plugins/auxly/skills"
SKILLS=(auxly-llm-council auxly-execute auxly-review auxly-meter auxly-digest auxly-board)
MARK_BEGIN="<!-- AUXLY-SKILLS:BEGIN (managed by install.sh — do not edit) -->"
MARK_END="<!-- AUXLY-SKILLS:END -->"

MODE="install"; LINK="symlink"; ONLY=""; DRY=0
for a in "$@"; do case "$a" in
  --uninstall) MODE="uninstall";; --copy) LINK="copy";; --claude-only) ONLY="claude";;
  --dry-run) DRY=1;; *) echo "unknown arg: $a" >&2; exit 1;; esac; done

say(){ printf '%s\n' "$*"; }
run(){ if [ "$DRY" = 1 ]; then say "  [dry-run] $*"; else eval "$@"; fi; }

# skills-native tools:  name|skills-dir
NATIVE=(
  "Claude Code|$HOME_DIR/.claude/skills"
  "OpenCode|$HOME_DIR/.config/opencode/skills"
  "Qwen|$HOME_DIR/.qwen/skills"
  "Kimi|$HOME_DIR/.kimi/skills"
)
# instruction-based tools:  name|detect-dir|context-file
INSTRUCT=(
  "Codex|$HOME_DIR/.codex|$HOME_DIR/.codex/AGENTS.md"
  "Gemini|$HOME_DIR/.gemini|$HOME_DIR/.gemini/GEMINI.md"
  "Antigravity (agy)|$HOME_DIR/.antigravity|$HOME_DIR/.antigravity/AGENTS.md"
  "Cursor|$HOME_DIR/.cursor|$HOME_DIR/.cursor/AGENTS.md"
)

install_native(){
  local name="$1" dir="$2" parent
  parent="$(dirname "$dir")"
  [ -d "$parent" ] || return 1
  say "• $name  → $dir"
  run "mkdir -p \"$dir\""
  for s in "${SKILLS[@]}"; do
    local target="$dir/$s"
    run "rm -rf \"$target\""
    if [ "$LINK" = copy ]; then run "cp -R \"$SKILLS_SRC/$s\" \"$target\""
    else run "ln -s \"$SKILLS_SRC/$s\" \"$target\""; fi
  done
  return 0
}

uninstall_native(){
  local name="$1" dir="$2" any=0; [ -d "$dir" ] || return 1
  for s in "${SKILLS[@]}"; do [ -e "$dir/$s" ] && { run "rm -rf \"$dir/$s\""; any=1; }; done
  [ "$any" = 1 ] && say "• $name  → removed skills from $dir"; return 0
}

adapter_block(){
  cat <<EOF
$MARK_BEGIN
## Auxly Skills
Auxly is a dev-loop tool suite (plan → execute → verify → review → recap) sharing one live browser
console. Installed at: \`$ROOT\` (export \`AUXLY_SKILLS_HOME=$ROOT\`).
Drive it via the shared CLI: \`python3 $ROOT/plugins/auxly/shared/console/console.py --help\`.
Full guide + per-skill workflows: \`$ROOT/AGENTS.md\` and \`$ROOT/plugins/auxly/skills/*/SKILL.md\`.
Use when the user wants multi-model planning, a live execution dashboard, code review, a token meter,
a run recap, or an all-runs board.
$MARK_END
EOF
}

install_instruct(){
  local name="$1" detect="$2" file="$3"
  [ -d "$detect" ] || return 1
  say "• $name  → $file"
  if [ "$DRY" = 1 ]; then say "  [dry-run] inject Auxly block"; return 0; fi
  mkdir -p "$(dirname "$file")"; touch "$file"
  python3 - "$file" "$MARK_BEGIN" "$MARK_END" <<'PY'
import sys, re, pathlib
f, b, e = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(f)
t = p.read_text(encoding="utf-8") if p.exists() else ""
t = re.sub(re.escape(b) + r".*?" + re.escape(e) + r"\n?", "", t, flags=re.S)
p.write_text(t.rstrip() + ("\n\n" if t.strip() else ""), encoding="utf-8")
PY
  adapter_block >> "$file"
  return 0
}

uninstall_instruct(){
  local name="$1" detect="$2" file="$3"; [ -f "$file" ] || return 1
  grep -q "AUXLY-SKILLS:BEGIN" "$file" || return 0
  if [ "$DRY" = 1 ]; then say "  [dry-run] strip Auxly block from $file"; return 0; fi
  python3 - "$file" "$MARK_BEGIN" "$MARK_END" <<'PY'
import sys, re, pathlib
f, b, e = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(f); t = p.read_text(encoding="utf-8")
t = re.sub(re.escape(b) + r".*?" + re.escape(e) + r"\n?", "", t, flags=re.S)
p.write_text(t.rstrip() + "\n", encoding="utf-8")
PY
  say "• $name  → removed Auxly block from $file"
}

say ""; say "Auxly Skills installer — source: $ROOT"; say ""
installed=0

if [ "$MODE" = uninstall ]; then
  for e in "${NATIVE[@]}"; do IFS='|' read -r n d <<<"$e"; uninstall_native "$n" "$d" || true; done
  for e in "${INSTRUCT[@]}"; do IFS='|' read -r n det f <<<"$e"; uninstall_instruct "$n" "$det" "$f" || true; done
  say ""; say "Uninstalled. Restart your tools."; exit 0
fi

if [ "$ONLY" = claude ]; then
  install_native "Claude Code" "$HOME_DIR/.claude/skills" && installed=1
else
  for e in "${NATIVE[@]}"; do IFS='|' read -r n d <<<"$e"; install_native "$n" "$d" && installed=$((installed+1)) || true; done
  for e in "${INSTRUCT[@]}"; do IFS='|' read -r n det f <<<"$e"; install_instruct "$n" "$det" "$f" && installed=$((installed+1)) || true; done
fi

say ""
say "Installed into $installed tool(s)."
say "Skills-native tools expose: /auxly-llm-council /auxly-execute /auxly-review /auxly-meter /auxly-digest /auxly-board"
say "Instruction-based tools: ask in plain language (\"run the auxly council\", \"execute with the dashboard\")."
say "Restart your tools to pick everything up."
