// @ts-check
/*
 * The bundle-composition gate (`blizzard-context:/verification/blizzard.md`'s
 * `web:bundle-composition` method) — a merge-gate check on the hub app's initial chunk,
 * the tooled half of `architecture/frontend-structure/eager-shell.md`
 * (`bzh:frontend-eager-shell-entry`).
 *
 * Builds the hub app with esbuild's own metafile (`ng build hub --stats-json`) and
 * resolves the initial chunk two ways from the same metafile:
 *
 * - At **output** granularity, for the size breakdown: the output whose `entryPoint` is
 *   the hub's `main.ts`, plus every output it reaches through a static
 *   (`kind: 'import-statement'`) edge, transitively. A `kind: 'dynamic-import'` edge
 *   starts a lazy chunk and is not followed.
 * - At **source-file** granularity, for forbidden-module detection and attribution: the
 *   same walk over the full module graph (`stats.json`'s top-level `inputs`), which also
 *   carries each file's own import edges — the output-level chunks say nothing about
 *   which *file* pulled a given module in.
 *
 * Fails when a source file reachable that eagerly is one esbuild cannot drop from the
 * initial chunk even when nothing on the eager path uses its exports (the barrel-leak
 * failure mode this check exists to catch): the fleet `chunk-detail/`, `garden/`,
 * `graphs/`, or `transcripts/` sub-barrels, `@dagrejs/*`, or `@angular/cdk`'s
 * `menu`/`overlay` bundles.
 *
 * Run from `web/`: `npm run bundle-check` (`node scripts/bundle-check.js`).
 */

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const ROOT = path.resolve(__dirname, '..');
const HUB_MAIN = 'projects/hub/src/main.ts';

/** Forbidden initial-chunk module patterns, each a predicate over a metafile-relative path
 * (forward slashes, no leading `./`, e.g. `projects/fleet/src/lib/garden/garden-page.ts`). */
const FORBIDDEN = [
  { name: 'fleet chunk-detail/', test: (f) => f.startsWith('projects/fleet/src/lib/chunk-detail/') },
  { name: 'fleet garden/', test: (f) => f.startsWith('projects/fleet/src/lib/garden/') },
  { name: 'fleet graphs/', test: (f) => f.startsWith('projects/fleet/src/lib/graphs/') },
  { name: 'fleet transcripts/', test: (f) => f.startsWith('projects/fleet/src/lib/transcripts/') },
  { name: '@dagrejs/*', test: (f) => f.startsWith('node_modules/@dagrejs/') },
  {
    name: '@angular/cdk menu/overlay',
    test: (f) => f.startsWith('node_modules/@angular/cdk/') && (f.includes('menu') || f.includes('overlay')),
  },
];

/** Areas named in the printed per-area breakdown, most-material first. Informational only —
 * `FORBIDDEN` above is what the gate actually enforces. */
const REPORT_AREAS = [
  { name: '@angular/core', test: (f) => f.startsWith('node_modules/@angular/core/') },
  { name: '@angular/cdk', test: (f) => f.startsWith('node_modules/@angular/cdk/') },
  { name: 'fleet chunk-detail/', test: (f) => f.startsWith('projects/fleet/src/lib/chunk-detail/') },
  { name: '@angular/router', test: (f) => f.startsWith('node_modules/@angular/router/') },
  { name: 'fleet garden/', test: (f) => f.startsWith('projects/fleet/src/lib/garden/') },
  { name: 'fleet kit/', test: (f) => f.startsWith('projects/fleet/src/lib/kit/') },
  { name: 'fleet api/', test: (f) => f.startsWith('projects/fleet/src/lib/api/') },
  { name: 'fleet graphs/', test: (f) => f.startsWith('projects/fleet/src/lib/graphs/') },
  { name: '@tanstack/query-core', test: (f) => f.includes('@tanstack/query-core') },
  { name: '@dagrejs/dagre + graphlib', test: (f) => f.startsWith('node_modules/@dagrejs/') || f.startsWith('node_modules/graphlib/') },
  { name: 'fleet transcripts/', test: (f) => f.startsWith('projects/fleet/src/lib/transcripts/') },
];

/** Every output reachable from the output whose `entryPoint` is `HUB_MAIN`, over static
 * (`import-statement`) edges only — the initial chunk, at output granularity.
 * @param {{ outputs: Record<string, any> }} meta */
function computeInitialOutputs(meta) {
  const outputs = meta.outputs;
  const mainKey = Object.keys(outputs).find((k) => outputs[k].entryPoint === HUB_MAIN);
  if (!mainKey) throw new Error(`bundle-check: no output in stats.json has entryPoint ${HUB_MAIN}`);

  const visited = new Set();
  const stack = [mainKey];
  while (stack.length > 0) {
    const cur = stack.pop();
    if (visited.has(cur)) continue;
    visited.add(cur);
    for (const imp of outputs[cur]?.imports ?? []) {
      if (imp.kind === 'import-statement' && !visited.has(imp.path)) stack.push(imp.path);
    }
  }
  return visited;
}

/** Every source file reachable from `HUB_MAIN` over static (`import-statement`) edges
 * only, at source-file granularity — the same initial-chunk walk `computeInitialOutputs`
 * does, but over the full module graph, so each forbidden hit still names its importer.
 * @param {{ inputs: Record<string, any> }} meta */
function computeEagerFiles(meta) {
  const inputs = meta.inputs;
  const visited = new Set();
  const parent = new Map();
  const stack = [HUB_MAIN];
  while (stack.length > 0) {
    const cur = stack.pop();
    if (visited.has(cur)) continue;
    visited.add(cur);
    for (const imp of inputs[cur]?.imports ?? []) {
      if (imp.kind !== 'import-statement' || imp.external) continue;
      if (!parent.has(imp.path)) parent.set(imp.path, cur);
      if (!visited.has(imp.path)) stack.push(imp.path);
    }
  }
  return { visited, parent };
}

/** The forbidden files among `eagerFiles`, each with the file that statically imports it.
 * @param {Set<string>} eagerFiles
 * @param {Map<string, string>} parent */
function findViolations(eagerFiles, parent) {
  const violations = [];
  for (const file of eagerFiles) {
    const hit = FORBIDDEN.find((f) => f.test(file));
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
      [HUB_MAIN]: {
        bytes: 10,
        imports: [{ path: 'projects/hub/src/app/app.ts', kind: 'import-statement' }],
      },
      'projects/hub/src/app/app.ts': {
        bytes: 10,
        imports: [
          { path: 'projects/fleet/src/lib/garden/garden-page.ts', kind: 'import-statement' }, // must-catch
          { path: 'projects/fleet/src/lib/kit/kit-button.ts', kind: 'import-statement' }, // must-pass: not forbidden
          { path: 'projects/fleet/src/lib/graphs/graph-page.ts', kind: 'dynamic-import' }, // must-pass: lazy, not eager
        ],
      },
      'projects/fleet/src/lib/garden/garden-page.ts': { bytes: 20, imports: [] },
      'projects/fleet/src/lib/kit/kit-button.ts': { bytes: 5, imports: [] },
      'projects/fleet/src/lib/graphs/graph-page.ts': { bytes: 20, imports: [] },
    },
  };

  const { visited, parent } = computeEagerFiles(fixture);
  const violations = findViolations(visited, parent);
  const files = violations.map((v) => v.file);

  if (!files.includes('projects/fleet/src/lib/garden/garden-page.ts')) {
    throw new Error(`bundle-composition detector missed an eager forbidden import (found: ${JSON.stringify(files)})`);
  }
  if (files.includes('projects/fleet/src/lib/graphs/graph-page.ts')) {
    throw new Error('bundle-composition detector false-positived on a module reached only through a dynamic import');
  }
  if (files.includes('projects/fleet/src/lib/kit/kit-button.ts')) {
    throw new Error('bundle-composition detector false-positived on a non-forbidden fleet module');
  }
  const hit = violations.find((v) => v.file === 'projects/fleet/src/lib/garden/garden-page.ts');
  if (hit?.importer !== 'projects/hub/src/app/app.ts') {
    throw new Error(`bundle-composition detector attributed the wrong importer: ${hit?.importer}`);
  }
}

function main() {
  assertBundleCompositionDetectorWorks();

  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bundle-check-'));
  let buildFailed = false;
  try {
    try {
      execFileSync('npx', ['ng', 'build', 'hub', '--output-path', outDir, '--stats-json'], {
        cwd: ROOT,
        stdio: 'inherit',
      });
    } catch {
      buildFailed = true;
    }

    const statsPath = path.join(outDir, 'stats.json');
    if (!fs.existsSync(statsPath)) {
      console.error('bundle-check: hub build produced no stats.json — see the build output above.');
      process.exitCode = 1;
      return;
    }
    const meta = JSON.parse(fs.readFileSync(statsPath, 'utf8'));

    const initialOutputs = computeInitialOutputs(meta);
    const { visited: eagerFiles, parent } = computeEagerFiles(meta);
    const violations = findViolations(eagerFiles, parent);
    const initialTotal = [...initialOutputs]
      .filter((o) => o.endsWith('.js'))
      .reduce((sum, o) => sum + (meta.outputs[o]?.bytes ?? 0), 0);

    console.log(`bundle-check: initial JS total ${formatBytes(initialTotal)} across ${initialOutputs.size} output(s).`);
    console.log('bundle-check: per-area breakdown of the initial chunk —');
    for (const { name, bytes } of areaBreakdown(meta, initialOutputs)) {
      if (bytes > 0) console.log(`  ${name}: ${formatBytes(bytes)}`);
    }

    if (violations.length > 0) {
      console.error('\nbundle-check: forbidden module(s) reachable from the initial chunk:\n');
      for (const v of violations) console.error(`  ${v.file} (${v.pattern}) — imported by ${v.importer}`);
      console.error(
        '\nRoute this behind a lazy page or an `@defer` block instead of an eager shell import — see ' +
          'architecture/frontend-structure/eager-shell.md (bzh:frontend-eager-shell-entry).',
      );
      process.exitCode = 1;
      return;
    }

    if (buildFailed) {
      console.error('bundle-check: hub build failed — see the build output above.');
      process.exitCode = 1;
      return;
    }

    console.log('bundle-check: initial chunk carries no forbidden module.');
  } finally {
    fs.rmSync(outDir, { recursive: true, force: true });
  }
}

main();
