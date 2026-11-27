// @ts-check
/*
 * The bundle-composition gate (`blizzard-context:/verification/blizzard.md`'s
 * `web:bundle-composition` method) — a merge-gate check on each app's initial chunk (the
 * hub's and the runner's), the tooled half of `architecture/frontend-structure/eager-shell.md`
 * (`bzh:frontend-eager-shell-entry`).
 *
 * Builds each app with esbuild's own metafile (`ng build <app> --stats-json`, which writes `browser-stats.json`) and
 * resolves its initial chunk two ways from the same metafile:
 *
 * - At **output** granularity, for the size breakdown: the output whose `entryPoint` is
 *   the app's `main.ts`, plus every output it reaches through a static
 *   (`kind: 'import-statement'`) edge, transitively. A `kind: 'dynamic-import'` edge
 *   starts a lazy chunk and is not followed.
 * - At **source-file** granularity, for forbidden-module detection and attribution: the
 *   same walk over the full module graph (`browser-stats.json`'s top-level `inputs`), which also
 *   carries each file's own import edges — the output-level chunks say nothing about
 *   which *file* pulled a given module in.
 *
 * Fails when a source file reachable that eagerly is one esbuild cannot drop from the
 * initial chunk even when nothing on the eager path uses its exports (the barrel-leak
 * failure mode this check exists to catch): the fleet `chunk/chunk-detail/`, `chunk/awaiting-human/`, or
 * `transcripts/` folders, a `fleet` weight-class entry file or the `fleet` root (the eager shell imports `fleet/shell`),
 * and `@angular/cdk`'s `menu`/`overlay`/`listbox` bundles in either app, and
 * for the hub also its `garden/`, `graphs/`, or `board/chunk-dock/` folders and `@dagrejs/*`.
 *
 * It also fails when the light `fleet/kit` entry's source closure reaches `@angular/cdk`, `@angular/aria`, or
 * `@angular/forms` (and when the hub build holds no such entry), and when a lazy entry's closure reaches the `fleet`
 * root (`bzh:frontend-weight-class-entries`).
 *
 * It also prints, per app, every lazy entry's static closure beyond the initial chunk with their median, and each
 * `fleet` entry file's source closure (`bundle-closure.js`).
 *
 * Run from `web/`: `npm run bundle-check` (`node scripts/bundle-check.js`).
 */

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const {
  entryFileCosts,
  lazyEntries,
  lightKitViolations,
  median,
  rootReachViolations,
  sourceClosure,
  staticOutputs,
} = require('./bundle-closure');

const ROOT = path.resolve(__dirname, '..');

/** Forbidden initial-chunk module patterns, each a predicate over a metafile-relative path
 * (forward slashes, no leading `./`, e.g. `projects/hub/src/app/garden/garden-page.ts`).
 * The fleet folders and the CDK stack are shared; the rest is per app. */
const SHARED_FORBIDDEN = [
  { name: 'fleet chunk-detail/', test: (f) => f.startsWith('projects/fleet/src/lib/chunk/chunk-detail/') },
  { name: 'fleet awaiting-human/', test: (f) => f.startsWith('projects/fleet/src/lib/chunk/awaiting-human/') },
  { name: 'fleet transcripts/', test: (f) => f.startsWith('projects/fleet/src/lib/transcripts/') },
  {
    name: 'fleet weight-class entry or root',
    test: (f) => f.startsWith('projects/fleet/src/entries/') || f === 'projects/fleet/src/public-api.ts',
  },
  {
    name: '@angular/cdk menu/overlay/listbox',
    test: (f) => f.startsWith('node_modules/@angular/cdk/') && (f.includes('menu') || f.includes('overlay') || f.includes('listbox')),
  },
];

/** The apps the gate builds, each with the `main.ts` its initial chunk starts from and the
 * forbidden patterns that bind it. */
const APPS = [
  {
    name: 'hub',
    main: 'projects/hub/src/main.ts',
    requiresLightKit: true,
    forbidden: [
      ...SHARED_FORBIDDEN,
      { name: 'hub chunk-dock/', test: (f) => f.startsWith('projects/hub/src/app/board/chunk-dock/') },
      { name: 'hub garden/', test: (f) => f.startsWith('projects/hub/src/app/garden/') },
      { name: 'hub graphs/', test: (f) => f.startsWith('projects/hub/src/app/graphs/') },
      { name: '@dagrejs/*', test: (f) => f.startsWith('node_modules/@dagrejs/') },
    ],
  },
  { name: 'runner', main: 'projects/runner/src/main.ts', requiresLightKit: false, forbidden: SHARED_FORBIDDEN },
];

/** Areas named in the printed per-area breakdown, most-material first. Informational only —
 * `FORBIDDEN` above is what the gate actually enforces. */
const REPORT_AREAS = [
  { name: '@angular/core', test: (f) => f.startsWith('node_modules/@angular/core/') },
  { name: '@angular/cdk', test: (f) => f.startsWith('node_modules/@angular/cdk/') },
  { name: 'fleet chunk-detail/', test: (f) => f.startsWith('projects/fleet/src/lib/chunk/chunk-detail/') },
  { name: '@angular/router', test: (f) => f.startsWith('node_modules/@angular/router/') },
  { name: 'hub garden/', test: (f) => f.startsWith('projects/hub/src/app/garden/') },
  { name: 'fleet kit/', test: (f) => f.startsWith('projects/fleet/src/lib/kit/') },
  { name: 'fleet api/', test: (f) => f.startsWith('projects/fleet/src/lib/api/') },
  { name: 'hub graphs/', test: (f) => f.startsWith('projects/hub/src/app/graphs/') },
  { name: '@tanstack/query-core', test: (f) => f.includes('@tanstack/query-core') },
  { name: '@dagrejs/dagre + graphlib', test: (f) => f.startsWith('node_modules/@dagrejs/') || f.startsWith('node_modules/graphlib/') },
  { name: 'fleet transcripts/', test: (f) => f.startsWith('projects/fleet/src/lib/transcripts/') },
];

/** The initial chunk at output granularity: every output the app's `main.ts` output reaches statically.
 * @param {{ outputs: Record<string, any> }} meta */
function computeInitialOutputs(meta, main) {
  const mainKey = Object.keys(meta.outputs).find((k) => meta.outputs[k].entryPoint === main);
  if (!mainKey) throw new Error(`bundle-check: no output in browser-stats.json has entryPoint ${main}`);
  return staticOutputs(meta, mainKey);
}

/** The forbidden files among `eagerFiles`, each with the file that statically imports it.
 * @param {Set<string>} eagerFiles
 * @param {Map<string, string>} parent
 * @param {{ name: string, test: (f: string) => boolean }[]} forbidden */
function findViolations(eagerFiles, parent, forbidden) {
  const violations = [];
  for (const file of eagerFiles) {
    const hit = forbidden.find((f) => f.test(file));
    if (hit) violations.push({ file, pattern: hit.name, importer: parent.get(file) ?? '<entry>' });
  }
  return violations;
}

/** Per-`REPORT_AREAS` byte totals across `initialOutputs`, summed from each output's own
 * `bytesInOutput` — the bundled (tree-shaken, minified) contribution, not raw source size.
 * @param {{ outputs: Record<string, any> }} meta
 * @param {Set<string>} initialOutputs */
function areaBreakdown(meta, initialOutputs) {
  const totals = REPORT_AREAS.map((a) => ({ name: a.name, bytes: 0 }));
  for (const outKey of initialOutputs) {
    for (const [file, m] of Object.entries(meta.outputs[outKey]?.inputs ?? {})) {
      const areaIndex = REPORT_AREAS.findIndex((a) => a.test(file));
      if (areaIndex !== -1) totals[areaIndex].bytes += /** @type {any} */ (m).bytesInOutput ?? 0;
    }
  }
  return totals;
}

/** Every lazy entry's closure and the median, then each `fleet` entry file's. Informational only.
 * @param {{ inputs: Record<string, any>, outputs: Record<string, any> }} meta
 * @param {string} app
 * @param {Set<string>} initialOutputs */
function reportClosures(meta, app, initialOutputs) {
  const entries = lazyEntries(meta, initialOutputs);
  console.log(`bundle-check: ${app} lazy entries, static closure beyond the initial chunk —`);
  for (const e of entries) {
    console.log(`  ${e.entry}: ${e.bytes} B`);
    if (e.heavy.length > 0) console.log(`    packages: ${e.heavy.join(', ')}`);
    if (e.areas.length > 0) console.log(`    fleet: ${e.areas.join(', ')}`);
  }
  console.log(`bundle-check: ${app} median of ${entries.length} lazy entries: ${median(entries.map((e) => e.bytes))} B`);
  console.log(`bundle-check: ${app} fleet entry files, closure beyond the initial chunk —`);
  for (const f of entryFileCosts(meta, initialOutputs)) {
    console.log(`  ${f.file}: ${f.bytes} B${f.heavy.length > 0 ? ` (${f.heavy.join(', ')})` : ''}`);
  }
}

/** @param {number} bytes */
function formatBytes(bytes) {
  return `${(bytes / 1000).toFixed(1)} kB`;
}

/**
 * Prove the forbidden-module detector can still fail, before trusting it over a real
 * build — the `assertRealTimerDetectorWorks` idiom `structural-gate.js` follows for each
 * of its own sweeps. The fixture pairs, on the same eager entry, one initial-chunk import
 * matching a forbidden pattern (must-catch), one that does not (must-pass), and the same
 * forbidden pattern reached only through a dynamic import (must-pass — a lazy chunk, not
 * the initial one) — so the walk is proven to stop at `dynamic-import` edges, not just to
 * pattern-match paths.
 */
function assertBundleCompositionDetectorWorks() {
  const fixture = {
    inputs: {
      [APPS[0].main]: {
        bytes: 10,
        imports: [{ path: 'projects/hub/src/app/shell/app.ts', kind: 'import-statement' }],
      },
      'projects/hub/src/app/shell/app.ts': {
        bytes: 10,
        imports: [
          { path: 'projects/hub/src/app/garden/garden-page.ts', kind: 'import-statement' }, // must-catch
          { path: 'projects/fleet/src/lib/kit/kit-button.ts', kind: 'import-statement' }, // must-pass: not forbidden
          { path: 'projects/hub/src/app/graphs/graph-page.ts', kind: 'dynamic-import' }, // must-pass: lazy, not eager
        ],
      },
      'projects/hub/src/app/garden/garden-page.ts': { bytes: 20, imports: [] },
      'projects/fleet/src/lib/kit/kit-button.ts': { bytes: 5, imports: [] },
      'projects/hub/src/app/graphs/graph-page.ts': { bytes: 20, imports: [] },
    },
  };

  const { visited, parent } = sourceClosure(fixture, APPS[0].main);
  const violations = findViolations(visited, parent, APPS[0].forbidden);
  const files = violations.map((v) => v.file);

  if (!files.includes('projects/hub/src/app/garden/garden-page.ts')) {
    throw new Error(`bundle-composition detector missed an eager forbidden import (found: ${JSON.stringify(files)})`);
  }
  if (files.includes('projects/hub/src/app/graphs/graph-page.ts')) {
    throw new Error('bundle-composition detector false-positived on a module reached only through a dynamic import');
  }
  if (files.includes('projects/fleet/src/lib/kit/kit-button.ts')) {
    throw new Error('bundle-composition detector false-positived on a non-forbidden fleet module');
  }
  const hit = violations.find((v) => v.file === 'projects/hub/src/app/garden/garden-page.ts');
  if (hit?.importer !== 'projects/hub/src/app/shell/app.ts') {
    throw new Error(`bundle-composition detector attributed the wrong importer: ${hit?.importer}`);
  }
  assertEagerEntryDetectorWorks();
  assertLightKitDetectorWorks();
  assertRootReachDetectorWorks();
}

/** The eager-shell condition: a value import of a weight-class entry or of the root on the eager path is caught; a
 * type-only one and `fleet/shell` pass. */
function assertEagerEntryDetectorWorks() {
  const main = APPS[0].main;
  const entry = 'projects/fleet/src/entries/core.ts';
  const typed = 'projects/fleet/src/entries/host.ts';
  const root = 'projects/fleet/src/public-api.ts';
  const shell = 'projects/fleet/src/shell-api.ts';
  const fixture = {
    inputs: {
      [main]: {
        imports: [
          { path: entry, kind: 'import-statement' },
          { path: typed, kind: 'import-statement', external: true },
          { path: root, kind: 'import-statement' },
          { path: shell, kind: 'import-statement' },
        ],
      },
    },
  };
  const { visited, parent } = sourceClosure(fixture, main);
  for (const app of APPS) {
    const found = findViolations(visited, parent, app.forbidden)
      .map((v) => v.file)
      .sort()
      .join();
    if (found !== [entry, root].join()) {
      throw new Error(`bundle-composition detector misjudged the eager ${app.name} fleet imports: ${found}`);
    }
  }
}

/** The light-kit condition: a heavy module reached through the entry is caught, a type-only edge to one passes, and
 * a build without the entry fails where the entry is required. */
function assertLightKitDetectorWorks() {
  const entry = 'projects/fleet/src/entries/kit.ts';
  const part = 'projects/fleet/src/lib/kit/kit-button.ts';
  const build = (/** @type {object[]} */ imports) => ({
    inputs: {
      [entry]: { bytes: 1, imports: [{ path: part, kind: 'import-statement' }] },
      [part]: { bytes: 1, imports },
    },
  });
  for (const pkg of ['cdk', 'aria', 'forms']) {
    const heavy = `node_modules/@angular/${pkg}/fesm2022/x.mjs`;
    const found = lightKitViolations(build([{ path: heavy, kind: 'import-statement' }]), true);
    if (found.length !== 1 || found[0].file !== heavy || found[0].importer !== part) {
      throw new Error(`bundle-composition detector missed @angular/${pkg} in the light kit: ${JSON.stringify(found)}`);
    }
  }
  const passing = build([
    { path: 'node_modules/@angular/forms/fesm2022/x.mjs', kind: 'import-statement', external: true },
    { path: 'node_modules/@angular/cdk/fesm2022/x.mjs', kind: 'dynamic-import' },
    { path: 'node_modules/@angular/core/fesm2022/x.mjs', kind: 'import-statement' },
  ]);
  if (lightKitViolations(passing, true).length !== 0) {
    throw new Error('bundle-composition detector false-positived on a type-only, lazy, or light import in the light kit');
  }
  const outside = '../shared/node_modules/@angular/cdk/fesm2022/x.mjs';
  if (lightKitViolations(build([{ path: outside, kind: 'import-statement' }]), true)[0]?.file !== outside) {
    throw new Error('bundle-composition detector missed a heavy package resolved outside web/');
  }
  if (lightKitViolations({ inputs: {} }, true).length !== 1) {
    throw new Error('bundle-composition detector passed a build with no light kit entry');
  }
  if (lightKitViolations({ inputs: {} }, false).length !== 0) {
    throw new Error('bundle-composition detector required the light kit entry of a build that owes none');
  }
}

/** The root condition: a lazy entry reaching the `fleet` root through any file is caught, naming the importer; one
 * reaching an entry, or only lazily importing the root's importer, passes. */
function assertRootReachDetectorWorks() {
  const root = 'projects/fleet/src/public-api.ts';
  const helper = 'projects/hub/src/app/helper.ts';
  const bad = 'projects/hub/src/app/bad.ts';
  const good = 'projects/hub/src/app/good.ts';
  const meta = {
    inputs: {
      [bad]: { imports: [{ path: helper, kind: 'import-statement' }] },
      [helper]: { imports: [{ path: root, kind: 'import-statement' }] },
      [good]: {
        imports: [
          { path: 'projects/fleet/src/entries/kit.ts', kind: 'import-statement' },
          { path: bad, kind: 'dynamic-import' },
        ],
      },
      [root]: { imports: [] },
    },
    outputs: {
      'main.js': {
        bytes: 1,
        imports: [
          { path: 'bad.js', kind: 'dynamic-import' },
          { path: 'good.js', kind: 'dynamic-import' },
        ],
      },
      'bad.js': { bytes: 1, entryPoint: bad, imports: [] },
      'good.js': { bytes: 1, entryPoint: good, imports: [] },
    },
  };
  const found = rootReachViolations(meta, new Set(['main.js']));
  if (found.length !== 1 || found[0].entry !== bad || found[0].importer !== helper) {
    throw new Error(`bundle-composition detector misjudged which lazy entry reaches the fleet root: ${JSON.stringify(found)}`);
  }
}

/**
 * Prove the lazy-closure walk: a lazy entry counts every output it reaches statically beyond the initial
 * ones, routes and `@defer` blocks alike, never an initial output nor one behind a further dynamic import.
 */
function assertLazyClosureReportWorks() {
  const meta = {
    inputs: {
      'projects/fleet/src/entries/kit.ts': {
        bytes: 1,
        imports: [
          { path: 'projects/fleet/src/lib/kit/kit-button.ts', kind: 'import-statement' },
          { path: 'node_modules/@angular/forms/x.mjs', kind: 'import-statement', external: true },
        ],
      },
      'projects/fleet/src/lib/kit/kit-button.ts': { bytes: 5, imports: [] },
    },
    outputs: {
      'main.js': {
        bytes: 100,
        imports: [
          { path: 'route.js', kind: 'dynamic-import' },
          { path: 'shared-initial.js', kind: 'import-statement' },
        ],
      },
      'shared-initial.js': { bytes: 50, imports: [] },
      'route.js': {
        bytes: 10,
        entryPoint: 'projects/hub/src/app/route.ts',
        inputs: { 'projects/hub/src/app/route.ts': {}, 'projects/fleet/src/lib/kit/kit-button.ts': { bytesInOutput: 4 } },
        imports: [
          { path: 'shared-initial.js', kind: 'import-statement' },
          { path: 'shared-lazy.js', kind: 'import-statement' },
          { path: 'deferred.js', kind: 'dynamic-import' },
        ],
      },
      'shared-lazy.js': {
        bytes: 20,
        inputs: { 'projects/fleet/src/lib/chunk/chunk-detail/a.ts': {}, 'node_modules/@angular/cdk/a.mjs': {} },
        imports: [],
      },
      'deferred.js': { bytes: 7, entryPoint: 'projects/hub/src/app/deferred.ts', imports: [] },
    },
  };
  const initial = computeInitialOutputs(
    { outputs: { ...meta.outputs, 'main.js': { ...meta.outputs['main.js'], entryPoint: 'm' } } },
    'm',
  );
  const found = lazyEntries(meta, initial).map((e) => `${e.entry}=${e.bytes}:${e.areas}:${e.heavy}`);
  const expected = [
    'projects/hub/src/app/route.ts=30:chunk/chunk-detail,kit:@angular/cdk',
    'projects/hub/src/app/deferred.ts=7::',
  ];
  if (found.join('|') !== expected.join('|')) {
    throw new Error(`bundle-composition lazy-closure walk read ${JSON.stringify(found)}`);
  }
  if (median([1, 9, 3]) !== 3 || median([1, 2, 3, 10]) !== 2.5) {
    throw new Error('bundle-composition median is wrong');
  }
  const [kit] = entryFileCosts(meta, initial);
  if (kit?.bytes !== 4 || kit.heavy.length !== 0) {
    throw new Error(`bundle-composition entry-file cost counted a type-only edge or missed a file: ${JSON.stringify(kit)}`);
  }
}

/** Build one app and check its initial chunk; resolves to whether it passed.
 * @param {{ name: string, main: string, requiresLightKit: boolean, forbidden: { name: string, test: (f: string) => boolean }[] }} app */
function checkApp(app) {
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), `bundle-check-${app.name}-`));
  let buildFailed = false;
  try {
    try {
      execFileSync('npx', ['ng', 'build', app.name, '--output-path', outDir, '--stats-json'], {
        cwd: ROOT,
        stdio: 'inherit',
      });
    } catch {
      buildFailed = true;
    }

    const statsPath = path.join(outDir, 'browser-stats.json');
    if (!fs.existsSync(statsPath)) {
      console.error(`bundle-check: ${app.name} build produced no browser-stats.json — see the build output above.`);
      return false;
    }
    const meta = JSON.parse(fs.readFileSync(statsPath, 'utf8'));

    const initialOutputs = computeInitialOutputs(meta, app.main);
    const { visited: eagerFiles, parent } = sourceClosure(meta, app.main);
    const violations = findViolations(eagerFiles, parent, app.forbidden);
    const initialTotal = [...initialOutputs]
      .filter((o) => o.endsWith('.js'))
      .reduce((sum, o) => sum + (meta.outputs[o]?.bytes ?? 0), 0);

    console.log(`bundle-check: ${app.name} initial JS total ${formatBytes(initialTotal)} across ${initialOutputs.size} output(s).`);
    console.log(`bundle-check: ${app.name} initial JS exactly ${initialTotal} B.`);
    reportClosures(meta, app.name, initialOutputs);
    console.log(`bundle-check: ${app.name} per-area breakdown of the initial chunk —`);
    for (const { name, bytes } of areaBreakdown(meta, initialOutputs)) {
      if (bytes > 0) console.log(`  ${name}: ${formatBytes(bytes)}`);
    }

    const heavyKit = lightKitViolations(meta, app.requiresLightKit);
    const rootReach = rootReachViolations(meta, initialOutputs);
    if (heavyKit.length > 0) {
      console.error(`\nbundle-check: the light fleet/kit entry reaches a heavy module in the ${app.name} build:\n`);
      for (const v of heavyKit) console.error(`  ${v.file} — imported by ${v.importer}`);
      console.error('\nMove the part to the fleet/kit/<family> entry that shares its footprint (bzh:frontend-weight-class-entries).');
    }
    if (rootReach.length > 0) {
      console.error(`\nbundle-check: lazy ${app.name} entries reach the fleet root:\n`);
      for (const v of rootReach) console.error(`  ${v.entry} — ${v.importer} imports it`);
      console.error('\nImport a fleet/<entry> path instead (bzh:frontend-weight-class-entries).');
    }

    if (violations.length > 0) {
      console.error(`\nbundle-check: forbidden module(s) reachable from the ${app.name} initial chunk:\n`);
      for (const v of violations) console.error(`  ${v.file} (${v.pattern}) — imported by ${v.importer}`);
      console.error(
        '\nRoute this behind a lazy page or an `@defer` block instead of an eager shell import — see ' +
          'architecture/frontend-structure/eager-shell.md (bzh:frontend-eager-shell-entry).',
      );
      return false;
    }

    if (buildFailed) {
      console.error(`bundle-check: ${app.name} build failed — see the build output above.`);
      return false;
    }

    if (heavyKit.length > 0 || rootReach.length > 0) return false;

    console.log(`bundle-check: ${app.name} initial chunk carries no forbidden module.`);
    return true;
  } finally {
    fs.rmSync(outDir, { recursive: true, force: true });
  }
}

function main() {
  assertBundleCompositionDetectorWorks();
  assertLazyClosureReportWorks();

  // Every app is checked before the verdict, so one run reports all the failures.
  const results = APPS.map(checkApp);
  if (results.includes(false)) process.exitCode = 1;
}

main();
