// @ts-check
/*
 * The structural gate (issue #78) — the tooled half of
 * `blizzard-context:/verification/blizzard.md`'s `web:structural-gate`
 * method.
 *
 * Two checks, both live:
 *
 *   1. A `max-lines` ceiling (the ~400-line cap, blizzard-context
 *      bzh:frontend-container-presentational) over every Angular component
 *      file (one declaring `@Component(`) — armed in phase 3 (#80) now that
 *      the chunk-detail decomposition (#79) and the panel splits (#80) have
 *      brought every in-scope file under the cap. `board-shell.ts`'s own
 *      standing gap over the cap (see history) closed with the `board-card`
 *      extraction (issue #137) — `MAX_LINES_EXEMPT_FILES` is empty again, so a
 *      *new* oversized file still fails the gate rather than being silently
 *      exempted by precedent.
 *   2. A real-timer sweep (issue #275) over the specs the `test` target runs:
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
 * The other check needs no equivalent — it fires on real files in the tree today, so
 * deleting it turns the gate red on its own.
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

  /** @type {{ file: string, lines: number }[]} */
  const lineViolations = [];
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
      // The max-lines check below is a component-source check; a spec is
      // neither a component nor a style host, so every spec stops here —
      // gating or not.
      continue;
    }

    if (isComponentFile(source) && !MAX_LINES_EXEMPT_FILES.includes(rel)) {
      const lines = countLines(source);
      if (lines > MAX_LINES) lineViolations.push({ file: rel, lines });
    }
  }

  if (lineViolations.length > 0) {
    console.error(`structural-gate: component files over the ${MAX_LINES}-line cap:\n`);
    for (const v of lineViolations) console.error(`  ${v.file}: ${v.lines} lines`);
    console.error(
      '\nDecompose into container + presentational siblings built from the kit, ' +
        'per blizzard-context:/architecture/frontend-structure/containers.md bzh:frontend-container-presentational.',
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

  console.log('structural-gate: max-lines ceiling and real-timer sweep both clean.');
}

main();
