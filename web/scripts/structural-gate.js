// @ts-check
/*
 * The structural gate (issue #78) — the tooled half of
 * `blizzard-harness:/verification/blizzard.md`'s `web:structural-gate`
 * method.
 *
 * Two halves, both live:
 *
 *   1. The chrome-duplication sweep (blizzard-harness bzh:frontend-kit): the
 *      retired `.panel`/`.p-hdr`/`.p-body`/`.status`/`.lbl` chrome-class
 *      blocks — the copy-pasted panel shell and async-state styling the
 *      `fleet/lib/kit/` components now own — come up empty in every
 *      component style outside the kit directory. The sweep only scans
 *      inline `styles: \`...\`` template literals (the codebase uses inline
 *      component styles exclusively); a separate `styleUrls` file would be
 *      outside this coverage.
 *   2. A `max-lines` ceiling (the ~400-line cap, blizzard-harness
 *      bzh:frontend-container-presentational) over every Angular component
 *      file (one declaring `@Component(`) — armed in phase 3 (#80) now that
 *      the chunk-detail decomposition (#79) and the panel splits (#80) have
 *      brought every in-scope file under the cap. `board-shell.ts` is a
 *      named, narrow exemption (see `MAX_LINES_EXEMPT_FILES`): it is over the
 *      cap today but out of both #79's and #80's file lists, so shrinking it
 *      is out of scope here and tracked as a standing gap instead of silently
 *      failing every future push.
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
 * - local-panel's `chunk-detail.ts` is the local-panel counterpart of the
 *   fleet chunk-detail monolith — `fleet/src/lib/chunk-detail/` came under
 *   the cap via its decomposition (blizzard#79); local-panel's own is
 *   deferred to #83's rename, and still carries residual `.lbl`/`.status`
 *   today.
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
  path.join('local-panel', 'src', 'lib', 'chunk-detail.ts'),
  path.join('local-panel', 'src', 'lib', 'heartbeat-freshness.ts'),
];

/** The `max-lines` ceiling every Angular component file is held to (the
 * ~400-line cap, blizzard-harness `bzh:frontend-container-presentational`). */
const MAX_LINES = 400;

/**
 * `max-lines` exemptions — deliberately narrow (named files, not directories),
 * so a *new* oversized file is still caught.
 *
 * - `board-shell.ts` (437 lines) predates this half's arming and is outside
 *   both #79's (chunk-detail) and #80's (runner/queue/questions/local-panel)
 *   file lists — shrinking it is a standing gap for a future pass, not
 *   silently exempted by omission. `BoardShell` is already presentational
 *   (not a container split), so the follow-up it needs is a further
 *   presentational sub-view extraction — e.g. a board-card child — not a
 *   container/presentational split.
 */
const MAX_LINES_EXEMPT_FILES = [path.join('fleet', 'src', 'lib', 'board-shell', 'board-shell.ts')];

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

/** Whether a source file declares an Angular component — the `max-lines`
 * ceiling applies only to these, not to every `.ts` file the sweep walks
 * (query/mutation/util files carry no template/style chrome to cap). */
function isComponentFile(source) {
  return source.includes('@Component(');
}

function countLines(source) {
  return source.split('\n').length;
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
  const chromeViolations = [];
  /** @type {{ file: string, lines: number }[]} */
  const lineViolations = [];

  for (const file of files) {
    const rel = path.relative(PROJECTS_DIR, file);
    const source = fs.readFileSync(file, 'utf8');

    if (!isExempt(rel)) {
      for (const block of extractStylesBlocks(source)) {
        RETIRED_PATTERN.lastIndex = 0;
        let match;
        while ((match = RETIRED_PATTERN.exec(block)) !== null) {
          chromeViolations.push({ file: rel, className: match[1] });
        }
      }
    }

    if (isComponentFile(source) && !MAX_LINES_EXEMPT_FILES.includes(rel)) {
      const lines = countLines(source);
      if (lines > MAX_LINES) lineViolations.push({ file: rel, lines });
    }
  }

  if (chromeViolations.length > 0) {
    console.error('structural-gate: retired chrome classes found outside fleet/lib/kit/:\n');
    for (const v of chromeViolations) console.error(`  ${v.file}: .${v.className}`);
    console.error(
      '\nAdopt the shared kit (fleet/lib/kit/ — KitPanel, KitAsyncState) instead of a local copy of this chrome, ' +
        'per blizzard-harness:/standards/frontend.md bzh:frontend-kit.',
    );
    process.exitCode = 1;
    return;
  }

  if (lineViolations.length > 0) {
    console.error(`structural-gate: component files over the ${MAX_LINES}-line cap:\n`);
    for (const v of lineViolations) console.error(`  ${v.file}: ${v.lines} lines`);
    console.error(
      '\nDecompose into container + presentational siblings built from the kit, ' +
        'per blizzard-harness:/architecture/frontend-structure.md bzh:frontend-container-presentational.',
    );
    process.exitCode = 1;
    return;
  }

  console.log('structural-gate: chrome-duplication sweep and max-lines ceiling both clean.');
}

main();
