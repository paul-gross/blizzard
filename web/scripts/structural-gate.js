// @ts-check
/*
 * The structural gate (issue #78, blizzard-harness bzh:frontend-kit) — the
 * tooled half of `blizzard-harness:/verification/blizzard.md`'s
 * `web:structural-gate` method.
 *
 * Two halves are planned; this script currently runs the first:
 *
 *   1. The chrome-duplication sweep (LIVE): the retired `.panel`/`.p-hdr`/
 *      `.p-body`/`.status`/`.lbl` chrome-class blocks — the copy-pasted panel
 *      shell and async-state styling the `fleet/lib/kit/` components now own
 *      — come up empty in every component style outside the kit directory.
 *   2. An eslint `max-lines` ceiling over component files (the ~400-line
 *      cap) — Gap (phase 3): armed once #79/#80 bring every file under the
 *      cap; arming it before then would fail the gate on files the epic has
 *      not yet shrunk.
 *
 * Run from `web/`: `npm run structural-gate` (`node scripts/structural-gate.js`).
 */

const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const PROJECTS_DIR = path.join(ROOT, 'projects');

/**
 * Directories the sweep does not enforce against, each with a reason:
 *
 * - `fleet/src/lib/kit` legitimately owns this chrome (issue #78 AC) — the
 *   whole point of the kit is one copy of it.
 * - `fleet/src/lib/chunk-detail` and local-panel's `chunk-detail.ts` are the
 *   chunk-detail monolith and its local-panel counterpart, explicitly out of
 *   Phase 1's adoption list (blizzard#78's Out of Scope) and deferred to the
 *   chunk-detail decomposition (blizzard#79) and #83's rename respectively —
 *   both still carry residual `.lbl`/`.status` today.
 * - local-panel's `heartbeat-freshness.ts` carries its own small `.lbl`
 *   ("hb") — a single-use bar label, not a panel/status block, and outside
 *   Phase 1's enumerated adoption list; noted as a further drift instance for
 *   a follow-up rather than folded into this phase.
 *
 * Narrow, file-level exclusions (not a directory-wide local-panel exemption)
 * so a *new* file with duplicated chrome elsewhere is still caught.
 */
const EXEMPT_DIRS = [path.join('fleet', 'src', 'lib', 'kit')];
const EXEMPT_FILES = [
  path.join('fleet', 'src', 'lib', 'chunk-detail', 'chunk-detail-panel.ts'),
  path.join('fleet', 'src', 'lib', 'chunk-detail', 'chunk-detail.ts'),
  path.join('local-panel', 'src', 'lib', 'chunk-detail.ts'),
  path.join('local-panel', 'src', 'lib', 'heartbeat-freshness.ts'),
];

// The retired chrome-class blocks (blizzard-harness bzh:frontend-kit Detect).
// Matched as a CSS class selector opener — the name as a whole word, directly
// followed by a compound-selector continuation (`.other`), a combinator, or
// the rule's opening brace — so `.status-icon` or `.panel-head` (a distinct,
// still-legitimate local class) don't false-positive.
const RETIRED_CLASSES = ['panel', 'p-hdr', 'p-body', 'status', 'lbl'];
const RETIRED_PATTERN = new RegExp(`\\.(${RETIRED_CLASSES.join('|')})(?![\\w-])\\s*[.,{]`, 'g');

/** @param {string} dir */
function walk(dir) {
  /** @type {string[]} */
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else if (entry.isFile() && entry.name.endsWith('.ts') && !entry.name.endsWith('.spec.ts')) out.push(full);
  }
  return out;
}

/** Every `styles: \`...\`` template-literal body in a component source file —
 * a component may have none (template-only) or one; ng-packagr components in
 * this codebase never use an array of style strings. */
function extractStylesBlocks(source) {
  const blocks = [];
  const re = /styles:\s*`([\s\S]*?)`/g;
  let match;
  while ((match = re.exec(source)) !== null) blocks.push(match[1]);
  return blocks;
}

function isExempt(relPath) {
  if (EXEMPT_FILES.includes(relPath)) return true;
  return EXEMPT_DIRS.some((dir) => relPath.startsWith(dir + path.sep));
}

function main() {
  const files = walk(PROJECTS_DIR).filter((f) => {
    const rel = path.relative(PROJECTS_DIR, f);
    // The generated API clients are never linted or held to house style
    // (bzh:generated-client) — they carry no component styles anyway, but
    // skip them explicitly rather than rely on that.
    return !rel.includes(path.join('lib', 'api') + path.sep);
  });

  /** @type {{ file: string, className: string }[]} */
  const violations = [];

  for (const file of files) {
    const rel = path.relative(PROJECTS_DIR, file);
    if (isExempt(rel)) continue;
    const source = fs.readFileSync(file, 'utf8');
    for (const block of extractStylesBlocks(source)) {
      RETIRED_PATTERN.lastIndex = 0;
      let match;
      while ((match = RETIRED_PATTERN.exec(block)) !== null) {
        violations.push({ file: rel, className: match[1] });
      }
    }
  }

  if (violations.length > 0) {
    console.error('structural-gate: retired chrome classes found outside fleet/lib/kit/:\n');
    for (const v of violations) console.error(`  ${v.file}: .${v.className}`);
    console.error(
      '\nAdopt the shared kit (fleet/lib/kit/ — KitPanel, KitAsyncState) instead of a local copy of this chrome, ' +
        'per blizzard-harness:/standards/frontend.md bzh:frontend-kit.',
    );
    process.exitCode = 1;
    return;
  }

  console.log('structural-gate: chrome-duplication sweep clean (max-lines half: Gap, phase 3).');
}

main();
