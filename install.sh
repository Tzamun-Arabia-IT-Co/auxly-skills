#!/usr/bin/env bash
# Auxly Skills — standalone installer.
# Symlinks (or copies) the bundled skills into your Claude Code skills directory
# so they work WITHOUT the plugin marketplace. Re-run any time to update.
#
#   ./install.sh            # symlink into ~/.claude/skills (default)
#   ./install.sh --copy     # copy instead of symlink
#   CLAUDE_SKILLS_DIR=/custom/path ./install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/plugins/auxly/skills"
DEST_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
MODE="symlink"
[ "${1:-}" = "--copy" ] && MODE="copy"

mkdir -p "$DEST_DIR"
echo "Installing Auxly skills into: $DEST_DIR  (mode: $MODE)"

for skill in "$SRC_DIR"/*/; do
  name="$(basename "$skill")"
  target="$DEST_DIR/$name"
  rm -rf "$target"
  if [ "$MODE" = "copy" ]; then
    cp -R "$skill" "$target"
  else
    ln -s "$skill" "$target"
  fi
  echo "  ✓ $name"
done

echo
echo "Done. Restart Claude Code (or run /skills) to pick up:"
echo "  /auxly-llm-council    — multi-model planning council"
echo "  /auxly-execute   — live execution dashboard"
