// @ts-check
/*
 * The structural gate (issue #78) — the tooled half of
 * `blizzard-context:/verification/blizzard.md`'s `web:structural-gate`
 * method.
 *
 * A real-timer sweep over the specs the `test` target runs: a
 * `setTimeout`/`setInterval` whose delay is a non-zero integer literal is a
 * real second spent inside the merge gate, and a window guessed rather than
 * chosen. `setTimeout(…, 0)` is the macrotask-flush idiom and is not matched;
 * `*.shell-sweep.spec.ts` is out of scope (a real frame wait is
 * `web:shell-sweep`'s method); a genuinely time-driven spec is named in
 * `REAL_TIMER_EXEMPT_FILES` with its reason.
 *
 * Also the kit floor (`blizzard-context:/architecture/frontend-structure/kit.md`
 * `bzh:frontend-kit-floor`): a component `.css` outside `fleet/lib/kit/` declaring
 * one of the kit's own retired chrome classes as a standalone rule, or a component
 * `.html` outside the kit hand-rolling `KitFactList`'s own `<dl class="kv">` grid.
 * A site that should not convert is named in `KIT_FLOOR_EXEMPT_SITES` with its reason.
 *
 * Run from `web/`: `npm run structural-gate` (`node scripts/structural-gate.js`).
 */

const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const PROJECTS_DIR = path.join(ROOT, 'projects');

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

/** Every file below `dir` whose name ends in one of `extensions`.
 * @param {string} dir
 * @param {readonly string[]} extensions
 */
function walk(dir, extensions) {
  /** @type {string[]} */
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full, extensions));
    else if (entry.isFile() && extensions.some((ext) => entry.name.endsWith(ext))) out.push(full);
  }
  return out;
}

/**
 * Prove the real-timer detector can still fail, before trusting it over the tree.
 *
 * Every gating spec today waits on fake timers, so the sweep finds nothing — and a check
 * that finds nothing is indistinguishable from a check that was deleted (`bzh:case-pins-
 * its-own-name`). These fixtures are the difference: each must-catch shape below is a
 * literal delay the sweep promises to fail, each must-pass shape is an escape it promises
 * to leave alone, and the gate refuses to run at all if the detector disagrees.
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

// The kit's own retired chrome classes — `KitPanel`'s panel shell
// (`.panel`/`.p-hdr`/`.p-body`/`.lbl`), `KitAsyncState`'s loading/error/empty triad
// (`.status`, and its own hand-rolled precursors `.none`/`.hint`/`.rest`). A component
// outside `fleet/lib/kit/` declaring one of these as a standalone rule has re-typed
// chrome the kit already owns (`bzh:frontend-kit-floor`).
const RETIRED_KIT_CLASSES = ['panel', 'p-hdr', 'p-body', 'lbl', 'status', 'none', 'hint', 'rest'];
const RETIRED_CLASS_RULE = new RegExp(`^\\s*\\.(${RETIRED_KIT_CLASSES.join('|')})\\s*\\{`, 'gm');

// `KitFactList`'s own two-column `<dl>` (`kit-fact-list.html`) — a hand-rolled
// `<dl class="kv">` outside the kit re-types the same grid.
const KV_FACT_GRID = /<dl[^>]*\bclass="kv"/;

const KIT_DIR_SEGMENT = path.join('fleet', 'src', 'lib', 'kit') + path.sep;

/**
 * A site that should not convert — a reasoned exemption per entry, the
 * `REAL_TIMER_EXEMPT_FILES` idiom:
 *
 * - `chunk-detail.css`'s `.rest` is the dock's always-mounted, full-height rest
 *   cover (flex-centered, its own gradient background and top border) — a
 *   different visual shape than any `KitAsyncState` placement renders, not a
 *   status line with a class name attached.
 * - `graph-diagram-detail.css`'s `.hint` is not an async state at all: nothing
 *   selected in the diagram viewer is local selection state, not a query's
 *   loading/error/empty.
 */
const KIT_FLOOR_EXEMPT_SITES = [
  { file: path.join('fleet', 'src', 'lib', 'chunk-detail', 'chunk-detail.css'), class: 'rest' },
  { file: path.join('fleet', 'src', 'lib', 'graphs', 'graph-diagram-detail.css'), class: 'hint' },
];

/**
 * Prove the kit-floor detectors can still fail, before trusting them over the tree —
 * the same reasoning `assertRealTimerDetectorWorks` follows.
 */
function assertKitFloorDetectorWorks() {
  const mustCatchClasses = [
    ['.panel {', 'panel'],
    ['.p-hdr {', 'p-hdr'],
    ['.p-body {', 'p-body'],
    ['.lbl {', 'lbl'],
    ['.status {', 'status'],
    ['.none {', 'none'],
    ['.hint {', 'hint'],
    ['.rest {', 'rest'],
    ['  .none {', 'none'], // indented, as every real rule is
  ];
  const mustPassClasses = [
    '.not-none {', // a different class name, not the retired one
    '.statusbar {', // ditto
    '.kv dd.zero {', // a compound/descendant selector, not a standalone retired class
    '.status.inline {', // the kit's own compound variant selector
  ];
  for (const [source, expected] of mustCatchClasses) {
    RETIRED_CLASS_RULE.lastIndex = 0;
    const match = RETIRED_CLASS_RULE.exec(source);
    if (match?.[1] !== expected) {
      throw new Error(`kit-floor class detector missed \`${source}\` (read ${match?.[1]}, expected ${expected})`);
    }
  }
  for (const source of mustPassClasses) {
    RETIRED_CLASS_RULE.lastIndex = 0;
    const match = RETIRED_CLASS_RULE.exec(source);
    if (match !== null) throw new Error(`kit-floor class detector false-positived on \`${source}\``);
  }

  const mustCatchGrids = ['<dl class="kv">', '<dl data-testid="x" class="kv" [attr.data-x]="y">'];
  const mustPassGrids = ['<dl class="kv-other">', '<dl class="other">', '<fleet-kit-fact-list class="kv" />'];
  for (const source of mustCatchGrids) {
    if (!KV_FACT_GRID.test(source)) throw new Error(`kit-floor fact-grid detector missed \`${source}\``);
  }
  for (const source of mustPassGrids) {
    if (KV_FACT_GRID.test(source)) throw new Error(`kit-floor fact-grid detector false-positived on \`${source}\``);
  }
}

/** Whether `relPath` (relative to `PROJECTS_DIR`) sits inside `fleet/lib/kit/` — the
 * kit's own sources are exempt from its own floor. */
function isInsideKit(relPath) {
  return relPath.startsWith(KIT_DIR_SEGMENT);
}

function main() {
  assertRealTimerDetectorWorks();
  assertKitFloorDetectorWorks();

  const specFiles = walk(PROJECTS_DIR, ['.ts']);

  /** @type {{ file: string, timer: string, delay: string }[]} */
  const realTimerViolations = [];

  for (const file of specFiles) {
    const rel = path.relative(PROJECTS_DIR, file);
    if (!rel.endsWith('.spec.ts') || !isGatingSpec(rel) || REAL_TIMER_EXEMPT_FILES.includes(rel)) continue;

    const source = fs.readFileSync(file, 'utf8');
    REAL_TIMER_CALL.lastIndex = 0;
    let match;
    while ((match = REAL_TIMER_CALL.exec(source)) !== null) {
      const delay = delayArgument(source, REAL_TIMER_CALL.lastIndex - 1);
      if (delay !== null) realTimerViolations.push({ file: rel, timer: match[1], delay });
    }
  }

  /** @type {{ file: string, class: string }[]} */
  const kitFloorViolations = [];

  for (const file of walk(PROJECTS_DIR, ['.css'])) {
    const rel = path.relative(PROJECTS_DIR, file);
    if (isInsideKit(rel)) continue;
    const source = fs.readFileSync(file, 'utf8');
    RETIRED_CLASS_RULE.lastIndex = 0;
    let match;
    while ((match = RETIRED_CLASS_RULE.exec(source)) !== null) {
      const cls = match[1];
      if (KIT_FLOOR_EXEMPT_SITES.some((e) => e.file === rel && e.class === cls)) continue;
      kitFloorViolations.push({ file: rel, class: cls });
    }
  }
  for (const file of walk(PROJECTS_DIR, ['.html'])) {
    const rel = path.relative(PROJECTS_DIR, file);
    if (isInsideKit(rel)) continue;
    const source = fs.readFileSync(file, 'utf8');
    if (KV_FACT_GRID.test(source)) kitFloorViolations.push({ file: rel, class: '<dl class="kv">' });
  }

  if (realTimerViolations.length > 0 || kitFloorViolations.length > 0) {
    if (realTimerViolations.length > 0) {
      console.error('structural-gate: real timers in merge-gating specs:\n');
      for (const v of realTimerViolations) console.error(`  ${v.file}: ${v.timer}(…, ${v.delay})`);
      console.error(
        '\nDrive the wait on fake timers (vi.useFakeTimers + vi.advanceTimersByTimeAsync) so the gating job spends ' +
          'no real seconds and the window is chosen rather than guessed; a `setTimeout(…, 0)` macrotask flush is ' +
          'fine, and a genuinely time-driven spec goes in REAL_TIMER_EXEMPT_FILES with a one-line reason.',
      );
    }
    if (kitFloorViolations.length > 0) {
      console.error('structural-gate: retired kit chrome outside fleet/lib/kit/:\n');
      for (const v of kitFloorViolations) console.error(`  ${v.file}: ${v.class}`);
      console.error(
        '\nCompose the shared kit (`KitPanel`/`KitAsyncState`/`KitFactList`, from `fleet`) instead of a re-typed ' +
          'copy; a site that genuinely should not convert goes in KIT_FLOOR_EXEMPT_SITES with a one-line reason.',
      );
    }
    process.exitCode = 1;
    return;
  }

  console.log('structural-gate: real-timer sweep clean.');
  console.log('structural-gate: kit floor clean.');
}

main();
