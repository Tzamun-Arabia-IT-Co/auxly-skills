#!/usr/bin/env bash
# Vendor the single-source shared console engine into every skill that uses it.
#
# Why vendor instead of symlink: the standalone installer symlinks each skill
# directory individually into ~/.claude/skills, so a skill can only rely on
# files that live *inside its own tree*. Each consuming skill therefore carries
# a copy of the console under scripts/_console. Edit the canonical source in
# plugins/auxly/shared/console/, then run this script to propagate it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$ROOT/plugins/auxly/shared/console"
SKILLS_DIR="$ROOT/plugins/auxly/skills"

# Skills that render into the shared Auxly Console.
CONSUMERS=(auxly-execute auxly-review auxly-board)

[ -d "$SRC" ] || { echo "missing console source: $SRC" >&2; exit 1; }

for name in "${CONSUMERS[@]}"; do
  dest="$SKILLS_DIR/$name/scripts/_console"
  [ -d "$SKILLS_DIR/$name" ] || continue   # skill not built yet — skip
  rm -rf "$dest"
  mkdir -p "$dest"
  cp -R "$SRC"/. "$dest"/
  find "$dest" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
  echo "  ✓ vendored console → skills/$name/scripts/_console"
done
echo "Done. Canonical source: plugins/auxly/shared/console/"
