// @ts-check
/*
 * The structural gate — the tooled half of
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
 * Also two mutation-hook shape sweeps, over every `.ts` file that calls `injectMutation(`:
 * an invalidation whose result is discarded via `void` rather than returned (so
 * `injectMutation`'s own settle machinery never awaits it), named in
 * `INVALIDATE_RETURNED_EXEMPT_FILES` when a genuine fire-and-forget is needed; and a direct
 * cache write (`setQueryData`, or an `onMutate` doing a snapshot/rollback) rather than
 * driving the cache through invalidation, named in `NO_CACHE_WRITE_EXEMPT_FILES` when
 * `onMutate` is used for a non-cache side effect instead.
 *
 * Also a repository-wide census keeps retired board Top/group controls out of
 * `projects/`, while leaving the generated grouping API available to other clients.
 *
 * Also the placement sweep (`placement-sweep.js`, `bzh:frontend-placement`): every
 * `fleet/src/lib/` unit must be reached by both apps, and no `fleet` file imports an app.
 *
 * Also the wire-conformist sweeps (`wire-conformist-sweep.js`), neither with an exemption
 * list: hand-written TS never cites a backend `.py` file, and a generated client function from
 * `fleet/src/lib/api/{hub,runner}/sdk.gen.ts` is named only inside a `*.query.ts` or
 * `*.mutations.ts` file.
 *
 * Also the package-layers sweep (`bzh:frontend-package-layers`), with no exemption list: each
 * project's source root is cut into folder units, and `LAYERED_PROJECTS` declares as data which
 * units each may import. An edge is an import, a `styleUrl`/`styleUrls`/`templateUrl`, or a CSS
 * `@import`. It fails an edge outside the table, a file in no declared unit, a relative import
 * into another project, a file in the root importing its own package entry (save a spec taking
 * the spec-support entry), and a cycle in a table.
 *
 * Finally, the containers-compose sweep (`containers-compose-sweep.js`,
 * `bzh:frontend-containers-compose`), with no exemption list: in a `@Component` class that calls a
 * query-bearing `inject*` helper, a `computed()` callback — and every same-class member it reaches
 * through `this` — holds no branch, loop, or collection transform; that derivation belongs in a pure
 * `*.model.ts` the container calls.
 *
 * Run from `web/`: `npm run structural-gate` (`node scripts/structural-gate.js`).
 */

const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const { assertPlacementDetectorWorks, placementViolations } = require('./placement-sweep');
const {
  assertBackendCitationDetectorWorks,
  backendCitationViolations,
  assertClientCallPlacementDetectorWorks,
  clientCallPlacementViolations,
} = require('./wire-conformist-sweep');
const {
  CONTAINERS_COMPOSE_HEADER,
  assertContainersComposeDetectorWorks,
  containersComposeViolations,
} = require('./containers-compose-sweep');

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
// (`.status`, and its own hand-rolled precursors `.none`/`.hint`/`.rest`), `KitButton`'s
// `.act`. A component outside `fleet/lib/kit/` declaring one of these as a standalone
// rule has re-typed chrome the kit already owns (`bzh:frontend-kit-floor`).
const RETIRED_KIT_CLASSES = ['panel', 'p-hdr', 'p-body', 'lbl', 'status', 'none', 'hint', 'rest', 'act'];
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
  { file: path.join('hub', 'src', 'app', 'board', 'chunk-dock', 'chunk-detail.css'), class: 'rest' },
  { file: path.join('hub', 'src', 'app', 'graphs', 'graph-diagram-detail.css'), class: 'hint' },
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
    ['.act {', 'act'],
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

// The board's retired Top/group contracts and the test handles that exposed them.
// The generated hub client intentionally retains its grouping endpoint; this census
// names only the former board facade, not that supported API surface.
const RETIRED_BOARD_CONTROL_SYMBOLS = [
  'BoardTopMove',
  'groupingControls',
  'moveToTop',
  'moveTopTestId',
  'topClicked',
  'groupSelected',
  'GroupVars',
  'injectGroupChunksMutation',
  'queue-select',
  'group-selected',
  'queue-move-top',
  'backlog-move-top',
];
const RETIRED_BOARD_CONTROL = new RegExp(`\\b(${RETIRED_BOARD_CONTROL_SYMBOLS.join('|')})\\b`, 'g');

/** The retired board-control symbols `source` still carries. */
function retiredBoardControls(source) {
  RETIRED_BOARD_CONTROL.lastIndex = 0;
  return [...source.matchAll(RETIRED_BOARD_CONTROL)].map((match) => match[1]);
}

/** Prove the board-control census catches every retired shape before using it on
 * `projects/` (`bzh:case-pins-its-own-name`). */
function assertBoardControlDetectorWorks() {
  for (const symbol of RETIRED_BOARD_CONTROL_SYMBOLS) {
    if (!retiredBoardControls(`const value = '${symbol}';`).includes(symbol)) {
      throw new Error(`board-control census missed \`${symbol}\``);
    }
  }
  for (const source of ['groupChunksApiChunksChunkIdGroupPost', 'reposition', 'board-reorder-grip']) {
    if (retiredBoardControls(source).length > 0) {
      throw new Error(`board-control census false-positived on \`${source}\``);
    }
  }
}

// The chunk detail dock's retired dependency-management UI — a free-text prerequisite
// field plus Declare/Release, which duplicated the hub API/CLI surface without adding
// anything the dock's own operator actions needed. The generated hub client and the hub
// CLI keep declaring/releasing a standing edge; this census names only the retired
// frontend affordance around them, not that supported surface.
const RETIRED_DOCK_CONTROL_SYMBOLS = [
  'dependency-prerequisite-input',
  'declare-dependency',
  'release-dependency',
  'DependencyEvent',
  'declareDependency',
  'releaseDependency',
  'DependencyVars',
  'injectDeclareDependencyMutation',
  'injectReleaseDependencyMutation',
];
const RETIRED_DOCK_CONTROL = new RegExp(`\\b(${RETIRED_DOCK_CONTROL_SYMBOLS.join('|')})\\b`, 'g');

/** The retired dock-control symbols `source` still carries. */
function retiredDockControls(source) {
  RETIRED_DOCK_CONTROL.lastIndex = 0;
  return [...source.matchAll(RETIRED_DOCK_CONTROL)].map((match) => match[1]);
}

/** Prove the dock-control census catches every retired shape before using it on
 * `projects/` (`bzh:case-pins-its-own-name`). */
function assertDockControlDetectorWorks() {
  for (const symbol of RETIRED_DOCK_CONTROL_SYMBOLS) {
    if (!retiredDockControls(`const value = '${symbol}';`).includes(symbol)) {
      throw new Error(`dock-control census missed \`${symbol}\``);
    }
  }
  for (const source of ['declareDependencyApiChunksChunkIdDependenciesPost', 'declared', 'dependency-list']) {
    if (retiredDockControls(source).length > 0) {
      throw new Error(`dock-control census false-positived on \`${source}\``);
    }
  }
}

/** Whether `relPath` (relative to `PROJECTS_DIR`) sits inside `fleet/lib/kit/` — the
 * kit's own sources are exempt from its own floor. */
function isInsideKit(relPath) {
  return relPath.startsWith(KIT_DIR_SEGMENT);
}

/** Whether `source` defines a mutation hook at all — both new sweeps below are scoped to
 * this file shape, the same signal-pairing `assertInvalidateReturnedDetectorWorks` and
 * `assertNoCacheWriteDetectorWorks` prove: neither pattern alone is forbidden, only inside
 * a file that also calls `injectMutation(`. */
function definesMutationHook(source) {
  return source.includes('injectMutation(');
}

/** The 1-based line number of `index` within `source`. */
function lineAt(source, index) {
  return source.slice(0, index).split('\n').length;
}

// A mutation hook's own invalidation, called with its result discarded, never lets
// `injectMutation`'s settle machinery await it — the mutation resolves before the query it
// just changed lands its refetch, so a caller that awaits `mutate()` and then reads the query
// sees stale data. The fixed form returns the promise (or folds it into a `Promise.all([...])`)
// instead. Scoped to files that define a mutation hook (`injectMutation(`) — the same call
// appears deliberately fire-and-forget elsewhere (`live-invalidation-spine.ts`'s SSE-driven
// bulk invalidation), which is not this rule's concern and carries no `injectMutation(` to trip
// the pairing.
//
// Two discard shapes, not one: an explicit `void` (the common style here, unambiguous
// wherever it sits — a concise arrow body `() => void queryClient.invalidateQueries()` is
// exactly as discarded when the call is a property value ending in `,` as when it is a
// statement ending in `;`) and a bare statement with no `void` at all, which only drops the
// promise when nothing else consumes it. Both matched under any client binding name, not
// just the literal `queryClient` every hook in this codebase happens to use today.
const VOID_INVALIDATE_QUERIES = /\bvoid\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\.invalidateQueries\s*\(/g;
const INVALIDATE_QUERIES_CALL = /([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\.invalidateQueries\s*\(/g;

/** The index just past the paren matching the `(` at `openIndex`, or `-1` if unbalanced. */
function matchingParenEnd(source, openIndex) {
  let depth = 0;
  for (let i = openIndex; i < source.length; i += 1) {
    if (source[i] === '(') depth += 1;
    else if (source[i] === ')') {
      depth -= 1;
      if (depth === 0) return i;
    }
  }
  return -1;
}

/** The nearest non-whitespace character at or after `index`, or `''` past the end. */
function nextNonSpace(source, index) {
  let i = index;
  while (i < source.length && /\s/.test(source[i])) i += 1;
  return source[i] ?? '';
}

/** Whether the un-`void`d `.invalidateQueries(` call starting at `callStart` sits at a bare
 * statement boundary — preceded (skipping whitespace) by `{`, `}`, `;`, `=>`, or the start of
 * the file, rather than by `return`, `await`, `=`, `(`, `[`, or `,`, each of which means the
 * call's result is still consumed somewhere upstream (a `return`, an `await`, an assignment,
 * or an argument/array element — one call among several inside `Promise.all([...])`, this
 * codebase's own multi-key invalidation idiom, is exactly this last case: each element is
 * followed by `,`, never a statement-terminating `;`). Also requires the call's closing paren
 * to be followed by that `;` — the same statement-boundary reasoning at the other end. */
function isBareDiscardedStatement(source, callStart, parenClose) {
  if (nextNonSpace(source, parenClose + 1) !== ';') return false;
  let k = callStart - 1;
  while (k >= 0 && /\s/.test(source[k])) k -= 1;
  if (k < 0) return true;
  const precedingWord = /(\w+)$/.exec(source.slice(0, k + 1))?.[1];
  if (precedingWord === 'return' || precedingWord === 'await') return false;
  if (source[k] === '=' || source[k] === '(' || source[k] === '[' || source[k] === ',') return false;
  if (source[k] === '{' || source[k] === '}' || source[k] === ';') return true;
  return source.slice(Math.max(0, k - 1), k + 1) === '=>';
}

/** A site that should keep discarding the invalidation promise — a reasoned exemption per
 * entry, the `REAL_TIMER_EXEMPT_FILES` idiom. Empty for now: every mutation hook found this
 * way is expected to return or await its invalidation instead. */
const INVALIDATE_RETURNED_EXEMPT_FILES = [];

/** The lines of `source` (a mutation-hook file) discarding an invalidation — `void`d or bare,
 * under any client binding name. */
function discardedInvalidationLines(source) {
  if (!definesMutationHook(source)) return [];
  const lines = [];

  VOID_INVALIDATE_QUERIES.lastIndex = 0;
  let match;
  while ((match = VOID_INVALIDATE_QUERIES.exec(source)) !== null) lines.push(lineAt(source, match.index));

  INVALIDATE_QUERIES_CALL.lastIndex = 0;
  while ((match = INVALIDATE_QUERIES_CALL.exec(source)) !== null) {
    const parenOpen = match.index + match[0].length - 1;
    const parenClose = matchingParenEnd(source, parenOpen);
    if (parenClose === -1) continue;
    if (isBareDiscardedStatement(source, match.index, parenClose)) lines.push(lineAt(source, match.index));
  }

  return lines;
}

/**
 * Prove the discarded-invalidation detector can still fail, before trusting it over the
 * tree — the same reasoning `assertRealTimerDetectorWorks` follows. Each must-catch fixture
 * carries both signals (`injectMutation(` plus the discarded `void` call); the must-pass
 * fixtures each drop exactly one signal — the returned form drops the `void`, the unrelated
 * helper drops `injectMutation(` — confirming the sweep needs both together, not either alone.
 */
function assertInvalidateReturnedDetectorWorks() {
  const mustCatch = [
    `export function injectThingMutation() {
      return injectMutation(() => ({
        onSuccess: () => {
          void queryClient.invalidateQueries({ queryKey: x });
        },
      }));
    }`,
    `export function injectThingMutation() {
      return injectMutation(() => ({
        onSettled: () =>   void   queryClient.invalidateQueries(),
      }));
    }`, // ragged whitespace, and the plain-call form
    `export function injectThingMutation() {
      return injectMutation(() => ({
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: x });
        },
      }));
    }`, // bare statement, no `void` at all
    `export function injectThingMutation() {
      return injectMutation(() => ({
        onSuccess: () => {
          void client.invalidateQueries({ queryKey: x });
        },
      }));
    }`, // a differently-named client binding, `void`d
    `export function injectThingMutation() {
      return injectMutation(() => ({
        onSuccess: () => {
          this.queryClient.invalidateQueries({ queryKey: x });
        },
      }));
    }`, // a differently-named (member-access) client binding, bare
  ];
  for (const source of mustCatch) {
    if (discardedInvalidationLines(source).length === 0) {
      throw new Error(`discarded-invalidation detector missed:\n${source}`);
    }
  }

  const mustPass = [
    `export function injectThingMutation() {
      return injectMutation(() => ({
        onSuccess: () => {
          return queryClient.invalidateQueries({ queryKey: x });
        },
      }));
    }`, // returned, not discarded
    `export function injectThingMutation() {
      return injectMutation(() => ({
        onSuccess: async () => {
          await queryClient.invalidateQueries({ queryKey: x });
        },
      }));
    }`, // awaited, not discarded
    `export function injectThingMutation() {
      return injectMutation(() => ({
        onSettled: (_data, _error, vars) =>
          Promise.all([
            queryClient.invalidateQueries({ queryKey: a }),
            queryClient.invalidateQueries({ queryKey: b(vars) }),
          ]),
      }));
    }`, // this codebase's own multi-key idiom — each call is an array element (`,`), not a
        // statement (`;`), even though neither is individually returned or awaited
    `export function injectThingMutation() {
      return injectMutation(() => ({
        onSuccess: () => {
          const pending = queryClient.invalidateQueries({ queryKey: x });
          return pending;
        },
      }));
    }`, // assigned, not discarded
    `export function someUnrelatedHelper() {
      void queryClient.invalidateQueries({ queryKey: x });
    }`, // no injectMutation( at all — not a mutation-hook file
    `export function someUnrelatedHelper() {
      queryClient.invalidateQueries({ queryKey: x });
    }`, // same, for the bare (non-`void`) form
  ];
  for (const source of mustPass) {
    if (discardedInvalidationLines(source).length > 0) {
      throw new Error(`discarded-invalidation detector false-positived on:\n${source}`);
    }
  }
}

// `setQueryData` writes the cache directly — a predictable outcome renders from a pending
// mutation's own variables instead (`fleet/src/lib/core/mutation-pending/`), never a guess written
// into the cache, so this is forbidden everywhere in production code, not just inside the
// hook that owns the mutation: a *consumer* of a mutation hook (a container calling
// `.mutate(vars, { onMutate: ... })`, say) can write the cache just as easily as the hook
// itself, and forbidding it only inside a file that literally contains `injectMutation(` would
// leave every consumer free to do it instead. Spec files are exempt — a spec legitimately
// stubs/spies on `queryClient.setQueryData` as test setup (e.g. `sse/fleet-live.spec.ts`),
// which writes no real cache.
const SET_QUERY_DATA = /\.setQueryData\s*\(/g;

// `onMutate` is where a hook typically snapshots the cache before an optimistic write, but it's
// not always this shape (a hook may use it for a non-cache side effect, like toggling a local UI
// signal) — a static sweep cannot reliably tell the two apart, so this half stays scoped to a
// file that defines a mutation hook (`injectMutation(`), where the ambiguity actually arises;
// a real non-cache use is named in `NO_CACHE_WRITE_EXEMPT_FILES` instead, mirroring the other
// sweeps' exemption-list idiom.
const ON_MUTATE = /\bonMutate\s*:/g;

/**
 * A site that should keep `onMutate` — a reasoned exemption per entry, the
 * `REAL_TIMER_EXEMPT_FILES` idiom:
 *
 * - `runner/src/app/core/identity/auth.query.ts`'s `injectRunnerLogoutMutation` uses `onMutate`
 *   only to flip a local in-flight signal (`logoutInFlightSignal.set(true)`) — no
 *   `setQueryData`, no snapshot/rollback of query data, so it's not the cache-write
 *   pattern this sweep forbids.
 */
const NO_CACHE_WRITE_EXEMPT_FILES = [path.join('runner', 'src', 'app', 'core', 'identity', 'auth.query.ts')];

/** The lines of `source` (any non-spec `.ts` file at `rel`) writing the cache, honoring
 * `NO_CACHE_WRITE_EXEMPT_FILES`. `setQueryData` is checked everywhere a mutation hook is
 * consumed, not only where one is defined; `onMutate` stays scoped to a defining file. */
function cacheWriteLines(source, rel) {
  if (rel.endsWith('.spec.ts') || NO_CACHE_WRITE_EXEMPT_FILES.includes(rel)) return [];
  const lines = [];

  SET_QUERY_DATA.lastIndex = 0;
  let match;
  while ((match = SET_QUERY_DATA.exec(source)) !== null) lines.push(lineAt(source, match.index));

  if (definesMutationHook(source)) {
    ON_MUTATE.lastIndex = 0;
    while ((match = ON_MUTATE.exec(source)) !== null) lines.push(lineAt(source, match.index));
  }

  return lines;
}

/**
 * Prove the cache-write detector can still fail, before trusting it over the tree — the
 * same reasoning `assertRealTimerDetectorWorks` follows. The exempted-file must-pass case
 * uses `NO_CACHE_WRITE_EXEMPT_FILES`'s own real entry, so a stale exemption (the file
 * changing shape without the list catching up) fails loudly here rather than silently.
 */
function assertNoCacheWriteDetectorWorks() {
  const mustCatch = [
    `export function injectThingMutation() {
      return injectMutation(() => ({
        onSuccess: () => queryClient.setQueryData(key, data),
      }));
    }`, // setQueryData inside the hook itself
    `export function injectThingMutation() {
      return injectMutation(() => ({
        onMutate: async (vars) => {
          const previous = queryClient.getQueryData(key);
          queryClient.setQueryData(key, vars);
          return { previous };
        },
      }));
    }`, // onMutate snapshot/rollback inside the hook itself
    `export class SomeContainer {
      private readonly thingMutation = injectThingMutation();
      protected act(): void {
        this.thingMutation.mutate(vars, {
          onSuccess: () => this.queryClient.setQueryData(key, data),
        });
      }
    }`, // setQueryData from a *consumer* of a hook, not the hook's own file
  ];
  for (const source of mustCatch) {
    if (cacheWriteLines(source, 'unexempted-fixture.ts').length === 0) {
      throw new Error(`cache-write detector missed:\n${source}`);
    }
  }

  const mustPass = [
    `export function injectThingMutation() {
      return injectMutation(() => ({
        onSettled: () => queryClient.invalidateQueries({ queryKey: x }),
      }));
    }`, // neither pattern
    `export function someUnrelatedHelper() {
      const onMutate = true;
    }`, // no setQueryData, and `onMutate` isn't the mutation-option shape (no colon-keyed use, and no injectMutation( besides)
  ];
  for (const source of mustPass) {
    if (cacheWriteLines(source, 'unexempted-fixture.ts').length > 0) {
      throw new Error(`cache-write detector false-positived on:\n${source}`);
    }
  }

  // A spec legitimately stubs/spies on `setQueryData` as test setup — exempt by filename,
  // regardless of the `NO_CACHE_WRITE_EXEMPT_FILES` list.
  const specFixture = `it('does something', () => {
    vi.spyOn(queryClient, 'setQueryData');
  });`;
  if (cacheWriteLines(specFixture, path.join('fleet', 'src', 'lib', 'sse', 'fleet-live.spec.ts')).length > 0) {
    throw new Error(`cache-write detector false-positived on a .spec.ts file:\n${specFixture}`);
  }

  for (const exemptRel of NO_CACHE_WRITE_EXEMPT_FILES) {
    const exemptSource = fs.readFileSync(path.join(PROJECTS_DIR, exemptRel), 'utf8');
    if (cacheWriteLines(exemptSource, 'not-the-exempted-path.ts').length === 0) {
      // Proves the exemption is doing real work: without it, this file's own source would
      // still trip the detector (still a mutation hook, still carries the pattern) — a
      // vacuous exemption (nothing left to suppress) would go unnoticed otherwise.
      throw new Error(`cache-write exemption is stale: \`${exemptRel}\` no longer carries the pattern it was exempted for`);
    }
    if (cacheWriteLines(exemptSource, exemptRel).length > 0) {
      throw new Error(`cache-write exemption did not suppress \`${exemptRel}\``);
    }
  }
}

// The package-layers sweep (`bzh:frontend-package-layers`). Each project's source root is cut
// into units — folders, keyed by their path below the root — and each unit maps to the units it
// may import (the backend's `tests/test_layering.py` shape). A unit may also always import the
// project's shared kernel, and a spec may also import the project's spec-support unit. A file
// belongs to the longest key prefixing its path; a key with child keys (`garden` beside
// `garden/core`) owns only the files directly inside it. Something two features need moves down
// into `core`; nothing imports `shell`. There is no exemption list: a new edge is a table change.
// The rule's tables own each graph and these mirror them, so an edge changes both together.

const FLEET_LAYERS = {
  api: [],
  kit: [],
  core: ['api', 'kit'],
  sse: ['api', 'core'],
  transcripts: ['api', 'core', 'kit'],
  chunk: ['api', 'core', 'kit', 'transcripts'],
  shell: ['api', 'core', 'kit'],
  testing: ['api'],
};

const HUB_LAYERS = {
  core: [],
  admin: [],
  board: ['runners'],
  demo: [],
  events: [],
  graphs: [],
  login: [],
  runners: [],
  'garden/core': [],
  'garden/proposals': ['garden/core'],
  'garden/runs': ['garden/core'],
  'garden/scopes': ['garden/core'],
  'garden/findings': ['garden/core', 'garden/proposals'],
  'garden/routines': ['garden/core', 'garden/runs', 'graphs'],
  garden: ['garden/findings', 'garden/proposals', 'garden/routines', 'garden/runs', 'garden/scopes'],
  shell: [
    'admin',
    'board',
    'demo',
    'events',
    'garden',
    'garden/findings',
    'garden/proposals',
    'garden/routines',
    'garden/runs',
    'garden/scopes',
    'graphs',
    'login',
    'runners',
  ],
};

const RUNNER_LAYERS = {
  core: [],
  asks: [],
  environments: [],
  events: [],
  machine: [],
  status: [],
  board: ['asks', 'environments', 'machine', 'status'],
  shell: ['board', 'events', 'machine'],
};

/**
 * @typedef {{ name: string, root: string, kernel: string | null, specSupport: string | null,
 *   layers: Record<string, string[]> }} LayeredProject
 * `name` is the folder under `projects/`; `root` is the source root, relative to `projects/`.
 */

/** @type {LayeredProject[]} */
const LAYERED_PROJECTS = [
  { name: 'fleet', root: 'fleet/src/lib', kernel: null, specSupport: 'testing', layers: FLEET_LAYERS },
  { name: 'hub', root: 'hub/src/app', kernel: 'core', specSupport: null, layers: HUB_LAYERS },
  { name: 'runner', root: 'runner/src/app', kernel: 'core', specSupport: null, layers: RUNNER_LAYERS },
];

const CSS_IMPORT = /@import\s+(?:url\(\s*)?['"]([^'"]+)['"]/g;

const COMPONENT_RESOURCE_KEYS = new Set(['styleUrl', 'styleUrls', 'templateUrl']);

/**
 * Every component resource path `source` names — the string literals under a `styleUrl`,
 * `styleUrls`, or `templateUrl` key, which Angular resolves relative to the file the way it
 * resolves an import.
 *
 * @param {string} source
 * @returns {string[]}
 */
function componentResourcePaths(source) {
  const sf = ts.createSourceFile('resource-scan.ts', source, ts.ScriptTarget.Latest, false, ts.ScriptKind.TS);
  /** @type {string[]} */
  const paths = [];
  /** @param {ts.Node} node */
  const visit = (node) => {
    if (
      ts.isPropertyAssignment(node) &&
      (ts.isIdentifier(node.name) || ts.isStringLiteral(node.name)) &&
      COMPONENT_RESOURCE_KEYS.has(node.name.text)
    ) {
      const values = ts.isArrayLiteralExpression(node.initializer) ? node.initializer.elements : [node.initializer];
      for (const value of values) {
        if (ts.isStringLiteral(value) || ts.isNoSubstitutionTemplateLiteral(value)) paths.push(value.text);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return paths;
}

/**
 * Every module specifier `source` names: static and type-only imports, `export … from`,
 * dynamic `import()`, a component's `styleUrl`/`styleUrls`/`templateUrl`, and CSS `@import`.
 * A template names none.
 *
 * @param {string} file
 * @param {string} source
 */
function importSpecifiers(file, source) {
  if (file.endsWith('.html')) return [];
  if (file.endsWith('.css')) return [...source.matchAll(CSS_IMPORT)].map((m) => m[1]);
  return [
    ...ts.preProcessFile(source, true, true).importedFiles.map((f) => f.fileName),
    ...componentResourcePaths(source),
  ];
}

/**
 * Whether bare `specifier` names one of `project`'s own package entries (`fleet`,
 * `fleet/shell`, …).
 *
 * @param {LayeredProject} project
 * @param {string} specifier
 */
function namesOwnEntry(project, specifier) {
  return specifier === project.name || specifier.startsWith(`${project.name}/`);
}

/**
 * The unit owning `rel` (a path below a project's source root), or `null` when no key owns it:
 * a file directly under the root, under an undeclared folder, or under an undeclared child of a
 * key that has child keys.
 *
 * @param {string} rel
 * @param {Record<string, string[]>} layers
 */
function layerUnitOf(rel, layers) {
  const segments = rel.split('/');
  let unit = null;
  for (let i = 1; i < segments.length; i++) {
    const key = segments.slice(0, i).join('/');
    if (Object.hasOwn(layers, key)) unit = key;
  }
  if (unit === null) return null;
  const ownsSubfolders = !Object.keys(layers).some((key) => key.startsWith(`${unit}/`));
  return ownsSubfolders || segments.length === unit.split('/').length + 1 ? unit : null;
}

/**
 * A cycle in `project`'s table, counting every unit's implicit edge to the kernel, as the
 * units along it (first repeated last) — or `null` when the table is acyclic.
 *
 * @param {LayeredProject} project
 */
function layerCycle(project) {
  const edges = (/** @type {string} */ unit) => [
    ...(project.layers[unit] ?? []),
    ...(project.kernel !== null && unit !== project.kernel ? [project.kernel] : []),
  ];
  /** @type {Map<string, 'open' | 'done'>} */
  const state = new Map();
  /** @type {string[]} */
  const stack = [];
  /** @param {string} unit @returns {string[] | null} */
  const visit = (unit) => {
    if (state.get(unit) === 'done') return null;
    if (state.get(unit) === 'open') return [...stack.slice(stack.indexOf(unit)), unit];
    state.set(unit, 'open');
    stack.push(unit);
    for (const next of edges(unit)) {
      const cycle = visit(next);
      if (cycle) return cycle;
    }
    stack.pop();
    state.set(unit, 'done');
    return null;
  };
  for (const unit of Object.keys(project.layers)) {
    const cycle = visit(unit);
    if (cycle) return cycle;
  }
  return null;
}

/**
 * Every package-layers violation over `files` (paths relative to `projects/`, forward slashes,
 * mapped to their source), one printable line each.
 *
 * @param {LayeredProject[]} projects
 * @param {Map<string, string>} files
 * @returns {string[]}
 */
function packageLayerViolations(projects, files) {
  /** @type {string[]} */
  const lines = [];
  const projectNames = new Set(projects.map((p) => p.name));

  for (const project of projects) {
    const keys = Object.keys(project.layers);
    for (const key of keys) {
      const present = [...files.keys()].some((f) => f.startsWith(`${project.root}/${key}/`));
      if (!present) lines.push(`  ${project.root}/${key}/: declared in the table but holds no file`);
      for (const dep of project.layers[key]) {
        if (!Object.hasOwn(project.layers, dep)) lines.push(`  ${project.root}: ${key} → ${dep}, an undeclared unit`);
      }
    }
    for (const unit of [project.kernel, project.specSupport]) {
      if (unit !== null && !Object.hasOwn(project.layers, unit)) {
        lines.push(`  ${project.root}: kernel or spec-support unit ${unit} is not declared`);
      }
    }
    const cycle = layerCycle(project);
    if (cycle) lines.push(`  ${project.root}: the table has a cycle ${cycle.join(' → ')}`);
  }

  for (const [file, source] of files) {
    const project = projects.find((p) => file.startsWith(`${p.name}/`));
    if (!project) continue;
    const underRoot = file.startsWith(`${project.root}/`);
    const unit = underRoot ? layerUnitOf(file.slice(project.root.length + 1), project.layers) : null;
    if (underRoot && unit === null) {
      lines.push(`  ${file}: in no unit — the root holds only declared unit folders`);
    }
    for (const specifier of importSpecifiers(file, source)) {
      if (!specifier.startsWith('.')) {
        // A file inside the source root reaching its own package entry steps around the table;
        // only a spec may take the spec-support entry that way.
        if (!underRoot || !namesOwnEntry(project, specifier)) continue;
        const specEntry = project.specSupport !== null && specifier === `${project.name}/${project.specSupport}`;
        if (!(specEntry && file.endsWith('.spec.ts'))) {
          lines.push(`  ${file}: imports its own package entry '${specifier}' — import the unit relatively`);
        }
        continue;
      }
      const base = path.posix.normalize(path.posix.join(path.posix.dirname(file), specifier));
      const targetProject = base.split('/')[0];
      if (base.startsWith('../') || !projectNames.has(targetProject)) continue;
      if (targetProject !== project.name) {
        lines.push(`  ${file}: imports '${specifier}' across projects — use the package entry point`);
        continue;
      }
      if (unit === null) continue;
      const target = [base, `${base}.ts`, `${base}/index.ts`, `${base}.css`].find((c) => files.has(c));
      if (!target || !target.startsWith(`${project.root}/`)) continue;
      const targetUnit = layerUnitOf(target.slice(project.root.length + 1), project.layers);
      if (targetUnit === null || targetUnit === unit || targetUnit === project.kernel) continue;
      if (file.endsWith('.spec.ts') && targetUnit === project.specSupport) continue;
      if (!(project.layers[unit] ?? []).includes(targetUnit)) {
        lines.push(`  ${file}: ${unit} → ${targetUnit} ('${specifier}') is not in ${unit}'s table`);
      }
    }
  }
  return lines;
}

/**
 * Prove the package-layers detector can still fail, before trusting it over the tree — the
 * same reasoning `assertRealTimerDetectorWorks` follows. The fixture is an in-memory project
 * with a planted table: each must-catch shape is a violation the sweep promises to fail, each
 * must-pass shape an edge it promises to leave alone.
 */
function assertPackageLayersDetectorWorks() {
  /** @type {LayeredProject} */
  const project = {
    name: 'app',
    root: 'app/src/app',
    kernel: 'core',
    specSupport: 'testing',
    layers: { core: [], testing: [], alpha: [], beta: ['alpha'], shell: ['alpha', 'beta'] },
  };
  const files = new Map([
    ['app/src/app/core/key.ts', 'export const key = 1;\n'],
    ['app/src/app/testing/stub.ts', 'export const stub = 1;\n'],
    ['app/src/app/alpha/a.ts', "import { b } from '../beta/b';\n"], // must-catch: undeclared alpha → beta
    // must-catch: alpha → beta through a component's stylesheet and template
    [
      'app/src/app/alpha/a-view.ts',
      "@Component({ styleUrls: ['./a-view.css', '../beta/b.css'], templateUrl: '../beta/b.html' })\n",
    ],
    ['app/src/app/alpha/a-view.css', ':host { display: block; }\n'],
    // must-catch: a file in the root reaching its own package entries; only a spec may take the spec-support one
    [
      'app/src/app/alpha/self.ts',
      "import { b } from 'app';\nimport { s } from 'app/shell';\nimport { stub } from 'app/testing';\n",
    ],
    [
      'app/src/app/beta/b.ts',
      "import { key } from '../core/key';\nimport { Component } from '@angular/core';\n" +
        "import 'apple-pie';\nexport { a } from '../alpha/a';\n",
    ], // must-pass: kernel, third-party, and a package whose name only begins with the project's
    ['app/src/app/beta/b.css', ':host { display: block; }\n'],
    ['app/src/app/beta/b.html', '<p>b</p>\n'],
    // must-pass: a stylesheet in the kernel, a template in the unit
    ['app/src/app/beta/b-card.ts', "@Component({ styleUrl: '../core/host.css', templateUrl: './b-card.html' })\n"],
    ['app/src/app/beta/b-card.html', '<p>card</p>\n'],
    ['app/src/app/core/host.css', ':host { display: block; }\n'],
    // must-pass: spec → spec support, relatively and through the spec-support entry
    [
      'app/src/app/beta/b.spec.ts',
      "import { stub } from '../testing/stub';\nimport { stub as viaEntry } from 'app/testing';\n",
    ],
    // must-pass: outside the root, the entry is the way in
    ['app/src/public-api.spec.ts', "import { b } from 'app';\n"],
    ['app/src/app/shell/app.ts', "const routes = [() => import('../beta/b')];\n"], // must-pass: declared, dynamic
    ['app/src/app/stray.ts', 'export const stray = 1;\n'], // must-catch: a file in no unit
    ['app/src/app/gamma/g.ts', 'export const g = 1;\n'], // must-catch: an undeclared folder
    ['app/src/app/core/far.ts', "import { x } from '../../../../lib/src/x';\n"], // must-catch: across projects
    ['lib/src/x.ts', 'export const x = 1;\n'],
  ]);
  const lib = { name: 'lib', root: 'lib/src', kernel: null, specSupport: null, layers: {} };
  const lines = packageLayerViolations([project, lib], files);
  const mustCatch = [
    'app/src/app/alpha/a.ts: alpha → beta',
    'app/src/app/stray.ts: in no unit',
    'app/src/app/gamma/g.ts: in no unit',
    'app/src/app/core/far.ts: imports',
    "app/src/app/alpha/a-view.ts: alpha → beta ('../beta/b.css')",
    "app/src/app/alpha/a-view.ts: alpha → beta ('../beta/b.html')",
    "app/src/app/alpha/self.ts: imports its own package entry 'app'",
    "app/src/app/alpha/self.ts: imports its own package entry 'app/shell'",
    "app/src/app/alpha/self.ts: imports its own package entry 'app/testing'",
  ];
  for (const needle of mustCatch) {
    if (!lines.some((line) => line.includes(needle))) {
      throw new Error(`package-layers detector missed ${needle} (found: ${JSON.stringify(lines)})`);
    }
  }
  const mustPass = [
    'beta/b.ts:',
    'beta/b.css:',
    'beta/b.html:',
    'beta/b-card',
    'core/host.css:',
    'beta/b.spec.ts:',
    'shell/app.ts:',
    'public-api.spec.ts:',
    'a-view.css:',
  ];
  for (const needle of mustPass) {
    if (lines.some((line) => line.includes(needle))) {
      throw new Error(`package-layers detector false-positived on ${needle} (found: ${JSON.stringify(lines)})`);
    }
  }

  const cyclic = { ...project, layers: { ...project.layers, core: ['alpha'] } }; // must-catch: kernel → alpha → kernel
  if (!layerCycle(cyclic)) throw new Error('package-layers detector missed a cycle through the kernel');
  const peerCycle = { ...project, layers: { ...project.layers, alpha: ['beta'] } }; // must-catch: alpha ⇄ beta
  if (!layerCycle(peerCycle)) throw new Error('package-layers detector missed a cycle between peer units');
  if (layerCycle(project)) throw new Error('package-layers detector found a cycle in an acyclic table');
}

function main() {
  assertRealTimerDetectorWorks();
  assertKitFloorDetectorWorks();
  assertBoardControlDetectorWorks();
  assertDockControlDetectorWorks();
  assertInvalidateReturnedDetectorWorks();
  assertNoCacheWriteDetectorWorks();
  assertPlacementDetectorWorks();
  assertBackendCitationDetectorWorks();
  assertClientCallPlacementDetectorWorks();
  assertPackageLayersDetectorWorks();
  assertContainersComposeDetectorWorks();

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
  /** @type {{ file: string, symbol: string }[]} */
  const boardControlViolations = [];
  /** @type {{ file: string, symbol: string }[]} */
  const dockControlViolations = [];
  /** @type {{ file: string, line: number }[]} */
  const invalidateDiscardedViolations = [];
  /** @type {{ file: string, line: number }[]} */
  const cacheWriteViolations = [];

  for (const file of walk(PROJECTS_DIR, ['.ts'])) {
    const rel = path.relative(PROJECTS_DIR, file);
    const source = fs.readFileSync(file, 'utf8');

    if (!INVALIDATE_RETURNED_EXEMPT_FILES.includes(rel)) {
      for (const line of discardedInvalidationLines(source)) invalidateDiscardedViolations.push({ file: rel, line });
    }
    for (const line of cacheWriteLines(source, rel)) cacheWriteViolations.push({ file: rel, line });
  }

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

  for (const file of walk(PROJECTS_DIR, ['.ts', '.html', '.css'])) {
    const rel = path.relative(PROJECTS_DIR, file);
    const source = fs.readFileSync(file, 'utf8');
    for (const symbol of retiredBoardControls(source)) {
      boardControlViolations.push({ file: rel, symbol });
    }
    for (const symbol of retiredDockControls(source)) {
      dockControlViolations.push({ file: rel, symbol });
    }
  }

  const placementLines = placementViolations();
  const backendCitationLines = backendCitationViolations();
  const clientCallLines = clientCallPlacementViolations();

  /** @type {Map<string, string>} */
  const layeredFiles = new Map();
  for (const project of LAYERED_PROJECTS) {
    for (const file of walk(path.join(PROJECTS_DIR, project.name, 'src'), ['.ts', '.css', '.html'])) {
      layeredFiles.set(path.relative(PROJECTS_DIR, file).split(path.sep).join('/'), fs.readFileSync(file, 'utf8'));
    }
  }
  const packageLayerLines = packageLayerViolations(LAYERED_PROJECTS, layeredFiles);
  const containersComposeLines = containersComposeViolations();

  if (
    realTimerViolations.length > 0 ||
    kitFloorViolations.length > 0 ||
    boardControlViolations.length > 0 ||
    dockControlViolations.length > 0 ||
    invalidateDiscardedViolations.length > 0 ||
    cacheWriteViolations.length > 0 ||
    placementLines.length > 0 ||
    backendCitationLines.length > 0 ||
    clientCallLines.length > 0 ||
    packageLayerLines.length > 0 ||
    containersComposeLines.length > 0
  ) {
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
    if (boardControlViolations.length > 0) {
      console.error('structural-gate: retired board controls under projects:\n');
      for (const v of boardControlViolations) console.error(`  ${v.file}: ${v.symbol}`);
      console.error(
        '\nKeep grouping at its supported API and CLI surfaces; board cards reorder through their whole-card drag only.',
      );
    }
    if (dockControlViolations.length > 0) {
      console.error('structural-gate: retired dock dependency-management controls under projects:\n');
      for (const v of dockControlViolations) console.error(`  ${v.file}: ${v.symbol}`);
      console.error(
        '\nThe dock dropped its own prerequisite field and Declare/Release buttons; reach for `chunk depend` and ' +
          '`chunk release-dependency` on the hub CLI, or the hub API those commands wrap, rather than rebuilding ' +
          'the affordance here.',
      );
    }
    if (invalidateDiscardedViolations.length > 0) {
      console.error('structural-gate: discarded invalidation in mutation hooks:\n');
      for (const v of invalidateDiscardedViolations) console.error(`  ${v.file}:${v.line}`);
      console.error(
        '\nReturn the invalidation (or fold it into a Promise.all([...])) so injectMutation\'s own settle machinery ' +
          "awaits it, instead of discarding it with `void`; a hook that genuinely must fire-and-forget its " +
          'invalidation goes in INVALIDATE_RETURNED_EXEMPT_FILES with a one-line reason.',
      );
    }
    if (cacheWriteViolations.length > 0) {
      console.error('structural-gate: cache writes in mutation hooks:\n');
      for (const v of cacheWriteViolations) console.error(`  ${v.file}:${v.line}`);
      console.error(
        '\nA mutation hook drives its query cache through invalidation, not a direct `setQueryData` or an ' +
          '`onMutate` snapshot/rollback; an `onMutate` that only touches a non-cache local side effect goes in ' +
          'NO_CACHE_WRITE_EXEMPT_FILES with a one-line reason.',
      );
    }
    if (placementLines.length > 0) {
      console.error('structural-gate: fleet units placed outside what both apps reach:\n');
      for (const line of placementLines) console.error(line);
      console.error(
        '\nfleet holds only what both apps reach: move a unit one app reaches into that app, delete a dead one, ' +
          'and never import an app from fleet; a reasoned exemption goes in PLACEMENT_EXEMPT_UNITS with a one-line reason.',
      );
    }
    if (backendCitationLines.length > 0) {
      console.error('structural-gate: backend .py citations in hand-written TS:\n');
      for (const line of backendCitationLines) console.error(line);
      console.error(
        '\nThe frontend conforms to the generated client and its types, not to the Python module serving the wire ' +
          'today: name the generated type or operation instead of the backend file.',
      );
    }
    if (clientCallLines.length > 0) {
      console.error('structural-gate: generated client functions outside *.query.ts / *.mutations.ts:\n');
      for (const line of clientCallLines) console.error(line);
      console.error(
        '\nCall the generated client only from a query or mutation hook in a `*.query.ts` / `*.mutations.ts` file, ' +
          'and inject that hook where the call is needed.',
      );
    }
    if (packageLayerLines.length > 0) {
      console.error('structural-gate: imports and folders outside the package-layer tables:\n');
      for (const line of packageLayerLines) console.error(line);
      console.error(
        '\nA folder imports only the units its project\'s table allows, plus the shared kernel: move what two ' +
          'features need down into `core`, never import `shell`, and keep every folder a declared unit; a genuinely ' +
          'new edge is a table change in LAYERED_PROJECTS, and the table stays acyclic.',
      );
    }
    if (containersComposeLines.length > 0) {
      console.error(`structural-gate: ${CONTAINERS_COMPOSE_HEADER}:\n`);
      for (const line of containersComposeLines) console.error(line);
      console.error(
        "\nA container's computed() only composes — it reads signals and query results and calls imported " +
          'functions. Move the branch, loop, or collection transform into a pure `*.model.ts` function beside the ' +
          'feature, unit-tested without TestBed, and keep the computed() field calling it; a derivation that is ' +
          'really a backend classification goes onto the wire instead.',
      );
    }
    process.exitCode = 1;
    return;
  }

  console.log('structural-gate: real-timer sweep clean.');
  console.log('structural-gate: kit floor clean.');
  console.log('structural-gate: retired board-control census clean.');
  console.log('structural-gate: retired dock-control census clean.');
  console.log('structural-gate: mutation-hook invalidation sweep clean.');
  console.log('structural-gate: mutation-hook cache-write sweep clean.');
  console.log('structural-gate: placement sweep clean.');
  console.log('structural-gate: backend-citation sweep clean.');
  console.log('structural-gate: client-call placement sweep clean.');
  console.log('structural-gate: package-layers sweep clean.');
  console.log('structural-gate: containers-compose sweep clean.');
}

main();
