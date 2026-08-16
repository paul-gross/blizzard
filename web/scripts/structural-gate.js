// @ts-check
/*
 * The structural gate (issue #78) — the tooled half of
 * `blizzard-context:/verification/blizzard.md`'s `web:structural-gate`
 * method.
 *
 * Four checks, all live:
 *
 *   1. The chrome-duplication sweep (blizzard-context bzh:frontend-kit): the
 *      retired `.panel`/`.p-hdr`/`.p-body`/`.status`/`.lbl` chrome-class
 *      blocks — the copy-pasted panel shell and async-state styling the
 *      `fleet/lib/kit/` components now own — come up empty in every
 *      component style outside the kit directory. The sweep only scans
 *      inline `styles: \`...\`` template literals (the codebase uses inline
 *      component styles exclusively); a separate `styleUrls` file would be
 *      outside this coverage.
 *   2. A `max-lines` ceiling (the ~400-line cap, blizzard-context
 *      bzh:frontend-container-presentational) over every Angular component
 *      file (one declaring `@Component(`) — armed in phase 3 (#80) now that
 *      the chunk-detail decomposition (#79) and the panel splits (#80) have
 *      brought every in-scope file under the cap. `board-shell.ts`'s own
 *      standing gap over the cap (see history) closed with the `board-card`
 *      extraction (issue #137) — `MAX_LINES_EXEMPT_FILES` is empty again, so a
 *      *new* oversized file still fails the gate rather than being silently
 *      exempted by precedent.
 *   3. An empty-state-without-the-kit sweep (blizzard#181, blizzard-context
 *      the new rule sibling to bzh:frontend-kit-floor in
 *      architecture/frontend-structure.md): a component outside
 *      `fleet/lib/kit/` that renders a `data-testid` matching `*-empty` must
 *      also reference `fleet-kit-async-state` somewhere in the same file —
 *      the file-level signal that its empty copy is gated by the triad
 *      rather than a bare length check — unless it is named in
 *      `EMPTY_STATE_EXEMPT_FILES`, each entry a view the blizzard#181 sweep
 *      confirmed is reachable only once its *parent* has already resolved
 *      (so the view's own empty copy can never render mid-load).
 *   4. A real-timer sweep (issue #275) over the specs the `test` target runs:
 *      a `setTimeout`/`setInterval` whose delay is a non-zero integer literal
 *      is a real second spent inside the merge gate, and a window guessed
 *      rather than chosen. `setTimeout(…, 0)` is the macrotask-flush idiom and
 *      is not matched; `*.shell-sweep.spec.ts` is out of scope (a real frame
 *      wait is `web:shell-sweep`'s method); a genuinely time-driven spec is
 *      named in `REAL_TIMER_EXEMPT_FILES` with its reason.
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
 *   deferred to #83's rename, and still carries a residual `.lbl` (the
 *   escalation resume box's label) today. Its own `*-empty` rest state now
 *   renders through `fleet-kit-async-state` directly (issue #318 review
 *   round 2), so it no longer needs `EMPTY_STATE_EXEMPT_FILES` below.
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
 * ~400-line cap, blizzard-context `bzh:frontend-container-presentational`). */
const MAX_LINES = 400;

/**
 * `max-lines` exemptions — deliberately narrow (named files, not directories),
 * so a *new* oversized file is still caught.
 *
 * None today: `board-shell.ts` (437 lines) was the one standing exemption,
 * closed by extracting its per-card markup into a `board-card` presentational
 * sibling (issue #137) rather than a container/presentational split —
 * `BoardShell` is already presentational, so its follow-up was a further
 * sub-view extraction, not a re-layering.
 */
const MAX_LINES_EXEMPT_FILES = [];

/**
 * `EMPTY_STATE_EXEMPT_FILES` — files carrying a `*-empty` `data-testid` with
 * no `fleet-kit-async-state` reference of their own, each confirmed by the
 * blizzard#181 sweep to be reachable only after a *parent* container's own
 * triad has already resolved (so the empty copy here can never render before
 * its data is known) — a rest state, not an unmediated query result.
 */
const EMPTY_STATE_EXEMPT_FILES = [
  // Children of `chunk-detail.ts`, itself gated by `ChunkDetailPanel`'s
  // `[detail]` input never arriving until the container's own detail query
  // resolves — these two render only once that happened.
  path.join('fleet', 'src', 'lib', 'chunk-detail', 'chunk-timeline.ts'),
  path.join('fleet', 'src', 'lib', 'chunk-detail', 'chunk-artifacts.ts'),
  // The routed chunk page's Artifacts tab — reachable only once `ChunkPage`'s
  // own detail read has resolved and handed it a `detail` input.
  path.join('hub', 'src', 'app', 'board', 'chunk', 'chunk-artifacts-tab.ts'),
  // The admin table — `admin-page.ts` wraps it in `fleet-kit-async-state`
  // itself; the table is the presentational leaf, not the container
  // (`bzh:frontend-container-presentational`), so it owns no triad of its own.
  path.join('fleet', 'src', 'lib', 'admin', 'users-table.ts'),
  // The diagram's node/edge inspector — its `*-empty` is a "nothing selected"
  // rest state inside a diagram `graph-detail.ts` already renders behind its
  // own triad, not a second query result.
  path.join('fleet', 'src', 'lib', 'graphs', 'graph-diagram-detail.ts'),
];

// The retired chrome-class blocks (blizzard-context bzh:frontend-kit Detect).
// Matched as a CSS class selector opener — the name as a whole word, directly
// followed by a compound-selector continuation (`.other`), a combinator, or
// the rule's opening brace — so `.status-icon` or `.panel-head` (a distinct,
// still-legitimate local class) don't false-positive.
const RETIRED_CLASSES = ['panel', 'p-hdr', 'p-body', 'status', 'lbl'];
const RETIRED_PATTERN = new RegExp(`\\.(${RETIRED_CLASSES.join('|')})(?![\\w-])\\s*[.,{]`, 'g');

// A `data-testid` naming an empty-state handle (blizzard#181's own naming
// convention throughout this sweep: `*-empty`).
const EMPTY_STATE_TESTID_PATTERN = /data-testid="[\w-]*-empty"/;

// A `setTimeout`/`setInterval` whose delay is a non-zero integer literal: real seconds
// spent inside the merge gate, and a window guessed rather than chosen (issue #275).
// `setTimeout(…, 0)` is the macrotask-flush idiom and is deliberately not matched, and a
// delay held in a variable or expression is out of reach — the one escape the matched form
// leaves, and the one `blizzard-context:/verification/blizzard/commands.md` claims.
const REAL_TIMER_CALL = /\b(setTimeout|setInterval)\s*\(/g;
const LITERAL_DELAY = /^[1-9][\d_]*$/;

/**
 * The delay argument of the timer call opening at `open` (the index of its `(`), or `null`
 * when the call is unterminated or its delay is not a literal.
 *
 * Scanned by balancing brackets rather than by a bounded-nesting regex: a callback body is
 * arbitrarily deep (`setTimeout(() => refresh(q.get()), 250)`), and a pattern that gives up
 * past one level of nesting silently under-matches the contract stated above — a false
 * negative in a merge gate, which is the one failure a gate must not have.
 *
 * @param {string} source
 * @param {number} open
 * @returns {string | null}
 */
function delayArgument(source, open) {
  let depth = 0;
  let lastComma = -1;
  for (let i = open; i < source.length; i += 1) {
    const ch = source[i];
    if (ch === '(' || ch === '[' || ch === '{') depth += 1;
    else if (ch === ')' || ch === ']' || ch === '}') {
      depth -= 1;
      if (depth === 0) {
        if (lastComma === -1) return null; // one-argument call — no delay at all
        const delay = source.slice(lastComma + 1, i).trim();
        return LITERAL_DELAY.test(delay) ? delay : null;
      }
    } else if (ch === ',' && depth === 1) lastComma = i;
  }
  return null; // unterminated — not this gate's error to raise
}

/**
 * Specs the `test` target actually runs — every project's `test` target excludes
 * `*.shell-sweep.spec.ts` (asserted by `test_every_test_target_excludes_the_shell_sweep_specs` in
 * `tests/test_web_test_targets.py`, since a project missing that exclude would run a real-Chromium
 * spec inside the merge gate *and* be exempt here at the same time). Those specs run
 * under `web:shell-sweep`, where a real frame wait is the method rather than a smell.
 *
 * @param {string} relPath
 */
function isGatingSpec(relPath) {
  return relPath.endsWith('.spec.ts') && !relPath.endsWith('.shell-sweep.spec.ts');
}

/**
 * Gating specs whose wait is genuinely time-driven, each with a reason:
 *
 * - `demo-director.spec.ts` drives the kiosk tour's forever-loop through a real
 *   router harness and asserts on the trail it walks; its waits are polls of that
 *   loop, sized off the tour's own measured cadence (issue #275), not sleeps
 *   standing in for a timer that could be advanced.
 */
const REAL_TIMER_EXEMPT_FILES = [path.join('hub', 'src', 'app', 'demo', 'demo-director.spec.ts')];

/** @param {string} dir */
function walk(dir) {
  /** @type {string[]} */
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else if (entry.isFile() && entry.name.endsWith('.ts')) out.push(full);
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

/**
 * Prove the real-timer detector can still fail, before trusting it over the tree.
 *
 * Every gating spec today waits on fake timers, so the sweep finds nothing — and a check
 * that finds nothing is indistinguishable from a check that was deleted (`bzh:case-pins-
 * its-own-name`). These fixtures are the difference: each must-catch shape below is a
 * literal delay the sweep promises to fail, each must-pass shape is an escape it promises
 * to leave alone, and the gate refuses to run at all if the detector disagrees.
 *
 * The other three checks need no equivalent — each fires on real files in the tree today,
 * so deleting one turns the gate red on its own.
 */
function assertRealTimerDetectorWorks() {
  const mustCatch = [
    ['setTimeout(() => done(), 500)', '500'],
    ['setInterval(poll, 250)', '250'],
    ['setTimeout(() => refresh(query.get()), 250)', '250'], // nested call in the callback
    ['setTimeout(function () { a(b(c(1))); }, 1_000)', '1_000'], // deeper, and a separator
    ['setTimeout(() => { obj = { k: [1, 2] }; }, 30)', '30'], // braces and brackets balance too
  ];
  const mustPass = [
    'setTimeout(() => done(), 0)', // the macrotask-flush idiom
    'setTimeout(() => done(), DELAY)', // a named window, chosen rather than guessed
    'setTimeout(() => done(), delay * 2)',
    'setTimeout(flush)', // no delay argument at all
  ];
  for (const [source, expected] of mustCatch) {
    REAL_TIMER_CALL.lastIndex = 0;
    const match = REAL_TIMER_CALL.exec(source);
    const found = match && delayArgument(source, REAL_TIMER_CALL.lastIndex - 1);
    if (found !== expected) {
      throw new Error(`real-timer detector missed \`${source}\` (read ${found}, expected ${expected})`);
    }
  }
  for (const source of mustPass) {
    REAL_TIMER_CALL.lastIndex = 0;
    const match = REAL_TIMER_CALL.exec(source);
    const found = match && delayArgument(source, REAL_TIMER_CALL.lastIndex - 1);
    if (found !== null) throw new Error(`real-timer detector false-positived on \`${source}\` (read ${found})`);
  }
}

function main() {
  assertRealTimerDetectorWorks();
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
  /** @type {string[]} */
  const emptyStateViolations = [];
  /** @type {{ file: string, timer: string, delay: string }[]} */
  const realTimerViolations = [];

  for (const file of files) {
    const rel = path.relative(PROJECTS_DIR, file);
    const source = fs.readFileSync(file, 'utf8');

    if (rel.endsWith('.spec.ts')) {
      if (isGatingSpec(rel) && !REAL_TIMER_EXEMPT_FILES.includes(rel)) {
        REAL_TIMER_CALL.lastIndex = 0;
        let match;
        while ((match = REAL_TIMER_CALL.exec(source)) !== null) {
          const delay = delayArgument(source, REAL_TIMER_CALL.lastIndex - 1);
          if (delay !== null) realTimerViolations.push({ file: rel, timer: match[1], delay });
        }
      }
      // The three sweeps below are component-source checks; a spec is neither a
      // component nor a style host, so *every* spec stops here — gating or not.
      continue;
    }

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

    if (
      !isExempt(rel) &&
      !EMPTY_STATE_EXEMPT_FILES.includes(rel) &&
      EMPTY_STATE_TESTID_PATTERN.test(source) &&
      !source.includes('fleet-kit-async-state')
    ) {
      emptyStateViolations.push(rel);
    }
  }

  if (chromeViolations.length > 0) {
    console.error('structural-gate: retired chrome classes found outside fleet/lib/kit/:\n');
    for (const v of chromeViolations) console.error(`  ${v.file}: .${v.className}`);
    console.error(
      '\nAdopt the shared kit (fleet/lib/kit/ — KitPanel, KitAsyncState) instead of a local copy of this chrome, ' +
        'per blizzard-context:/standards/frontend.md bzh:frontend-kit.',
    );
    process.exitCode = 1;
    return;
  }

  if (lineViolations.length > 0) {
    console.error(`structural-gate: component files over the ${MAX_LINES}-line cap:\n`);
    for (const v of lineViolations) console.error(`  ${v.file}: ${v.lines} lines`);
    console.error(
      '\nDecompose into container + presentational siblings built from the kit, ' +
        'per blizzard-context:/architecture/frontend-structure.md bzh:frontend-container-presentational.',
    );
    process.exitCode = 1;
    return;
  }

  if (emptyStateViolations.length > 0) {
    console.error('structural-gate: *-empty data-testid rendered without fleet-kit-async-state:\n');
    for (const rel of emptyStateViolations) console.error(`  ${rel}`);
    console.error(
      '\nGate the empty copy behind fleet-kit-async-state (query-state.ts derives the state), or — if this ' +
        'view is reachable only after a parent has already resolved — add it to EMPTY_STATE_EXEMPT_FILES with ' +
        'a one-line reason, per blizzard-context:/architecture/frontend-structure.md.',
    );
    process.exitCode = 1;
    return;
  }

  if (realTimerViolations.length > 0) {
    console.error('structural-gate: real timers in merge-gating specs:\n');
    for (const v of realTimerViolations) console.error(`  ${v.file}: ${v.timer}(…, ${v.delay})`);
    console.error(
      '\nDrive the wait on fake timers (vi.useFakeTimers + vi.advanceTimersByTimeAsync) so the gating job spends ' +
        'no real seconds and the window is chosen rather than guessed; a `setTimeout(…, 0)` macrotask flush is ' +
        'fine, and a genuinely time-driven spec goes in REAL_TIMER_EXEMPT_FILES with a one-line reason.',
    );
    process.exitCode = 1;
    return;
  }

  console.log(
    'structural-gate: chrome-duplication sweep, max-lines ceiling, empty-state sweep, and real-timer sweep all clean.',
  );
}

main();
