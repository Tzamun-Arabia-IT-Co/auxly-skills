#!/usr/bin/env node
/*
 * Auxly Skills — npx launcher.
 *
 *   npx github:Tzamun-Arabia-IT-Co/auxly-skills            # install into all tools
 *   npx github:Tzamun-Arabia-IT-Co/auxly-skills --dry-run  # preview
 *   npx github:Tzamun-Arabia-IT-Co/auxly-skills --uninstall
 *
 * npx runs from an ephemeral checkout, so we copy the suite into a stable home
 * (~/.auxly-skills) and run the real installer from there — that way the skill
 * symlinks point at a permanent location, not a temp dir that npx will delete.
 */
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const SRC = path.resolve(__dirname, '..');
const HOME = process.env.AUXLY_SKILLS_HOME
  ? path.resolve(process.env.AUXLY_SKILLS_HOME)
  : path.join(os.homedir(), '.auxly-skills');
const args = process.argv.slice(2).filter((a) => a !== '--from-postinstall');

const SKIP = new Set(['.git', 'node_modules', '.github', 'auxly-console', 'auxly-dash',
  'auxly-council', 'llm-council', '__pycache__']);

function copyDir(src, dst) {
  fs.mkdirSync(dst, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    if (SKIP.has(entry.name)) continue;
    const s = path.join(src, entry.name);
    const d = path.join(dst, entry.name);
    if (entry.isDirectory()) copyDir(s, d);
    else if (entry.isSymbolicLink()) { /* skip symlinks; sync-console regenerates */ }
    else fs.copyFileSync(s, d);
  }
}

function main() {
  // If we're already running from the stable home, don't re-copy onto ourselves.
  if (path.resolve(SRC) !== HOME) {
    console.log(`Auxly: staging suite into ${HOME} …`);
    fs.rmSync(HOME, { recursive: true, force: true });
    copyDir(SRC, HOME);
  }
  const installer = path.join(HOME, 'install.sh');
  if (!fs.existsSync(installer)) {
    console.error(`Auxly: installer missing at ${installer}`);
    process.exit(1);
  }
  // Re-vendor the shared console into each skill (in case symlinks were skipped).
  const sync = path.join(HOME, 'sync-console.sh');
  if (fs.existsSync(sync)) spawnSync('bash', [sync], { stdio: 'inherit' });
  const r = spawnSync('bash', [installer, ...args], { stdio: 'inherit' });
  process.exit(r.status == null ? 0 : r.status);
}

main();
