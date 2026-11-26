// @ts-check
/*
 * The closure walks of the bundle-composition gate (`bundle-check.js`) over esbuild's metafile: what each lazy
 * entry downloads beyond the initial chunk, and what each `fleet` entry file's source closure reaches.
 * `bundle-check.js` runs these and proves them with fixtures; this module only exports.
 */

const FLEET_LIB = 'projects/fleet/src/lib/';
const ENTRIES_DIR = 'projects/fleet/src/entries/';

/** The `@angular` packages whose weight the entry split keeps out of light entries. */
const HEAVY_PACKAGES = ['@angular/cdk', '@angular/aria', '@angular/forms'];

/** Every output reachable from `start` over static (`import-statement`) edges only.
 * @param {{ outputs: Record<string, any> }} meta
 * @param {string} start */
function staticOutputs(meta, start) {
  const visited = new Set();
  const stack = [start];
  while (stack.length > 0) {
    const cur = /** @type {string} */ (stack.pop());
    if (visited.has(cur) || !meta.outputs[cur]) continue;
    visited.add(cur);
    for (const imp of meta.outputs[cur].imports ?? []) {
      if (imp.kind === 'import-statement' && !imp.external && !visited.has(imp.path)) stack.push(imp.path);
    }
  }
  return visited;
}

/** Every source file reachable from `start` over static edges only, with each file's first importer.
 * An `external` edge is skipped: esbuild marks a type-only import that way.
 * @param {{ inputs: Record<string, any> }} meta
 * @param {string} start */
function sourceClosure(meta, start) {
  const visited = new Set();
  /** @type {Map<string, string>} */
  const parent = new Map();
  const stack = [start];
  while (stack.length > 0) {
    const cur = /** @type {string} */ (stack.pop());
    if (visited.has(cur)) continue;
    visited.add(cur);
    for (const imp of meta.inputs[cur]?.imports ?? []) {
      if (imp.kind !== 'import-statement' || imp.external) continue;
      if (!parent.has(imp.path)) parent.set(imp.path, cur);
      if (!visited.has(imp.path)) stack.push(imp.path);
    }
  }
  return { visited, parent };
}

/** The heavy `@angular` packages among `files`.
 * @param {Iterable<string>} files */
function heavyPackages(files) {
  const found = new Set();
  for (const file of files) {
    const hit = HEAVY_PACKAGES.find((pkg) => file.startsWith(`node_modules/${pkg}/`));
    if (hit) found.add(hit);
  }
  return HEAVY_PACKAGES.filter((pkg) => found.has(pkg));
}

/** The `fleet` areas among `files`: each file's folder under `lib/`, two segments deep under a grouping folder.
 * @param {Iterable<string>} files */
function fleetAreas(files) {
  const areas = new Set();
  for (const file of files) {
    if (!file.startsWith(FLEET_LIB)) continue;
    const parts = file.slice(FLEET_LIB.length).split('/');
    const depth = ['core', 'chunk', 'shell', 'api'].includes(parts[0]) ? 2 : 1;
    areas.add(parts.slice(0, depth).join('/'));
  }
  return [...areas].sort();
}

/** The middle value of `values`, the mean of the two middle ones for an even count.
 * @param {number[]} values */
function median(values) {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/**
 * Every dynamic-import target, route and `@defer` block alike: the bytes its static output closure adds
 * beyond the initial outputs, and the `fleet` areas and heavy packages that added closure reaches.
 *
 * @param {{ inputs: Record<string, any>, outputs: Record<string, any> }} meta
 * @param {Set<string>} initialOutputs
 */
function lazyEntries(meta, initialOutputs) {
  const targets = new Set();
  for (const output of Object.values(meta.outputs)) {
    for (const imp of output.imports ?? []) {
      if (imp.kind === 'dynamic-import' && !imp.external && !initialOutputs.has(imp.path)) targets.add(imp.path);
    }
  }
  return [...targets]
    .filter((key) => key.endsWith('.js') && meta.outputs[key]?.entryPoint)
    .map((key) => {
      const extra = [...staticOutputs(meta, key)].filter((o) => !initialOutputs.has(o) && o.endsWith('.js'));
      const inputs = new Set(extra.flatMap((o) => Object.keys(meta.outputs[o]?.inputs ?? {})));
      return {
        entry: /** @type {string} */ (meta.outputs[key].entryPoint),
        bytes: extra.reduce((sum, o) => sum + meta.outputs[o].bytes, 0),
        areas: fleetAreas(inputs),
        heavy: heavyPackages(inputs),
      };
    })
    .sort((a, b) => b.bytes - a.bytes || a.entry.localeCompare(b.entry));
}

/** Is `file` a `fleet` entry file: a public surface an app imports `fleet` through, or a barrel under `lib/`.
 * @param {string} file */
function isEntryFile(file) {
  if (file.startsWith(ENTRIES_DIR)) return true;
  if (/^projects\/fleet\/src\/[\w-]*(public-api|shell-api)\.ts$/.test(file)) return true;
  return file.startsWith(FLEET_LIB) && !file.startsWith(`${FLEET_LIB}api/`) && file.endsWith('/index.ts');
}

/**
 * Each `fleet` entry file in the build: the bundled (minified) bytes its static source closure holds
 * outside the initial outputs, and the heavy packages that closure reaches. A file's bytes are what the
 * build actually kept of it; an entry file is not an output of its own.
 *
 * @param {{ inputs: Record<string, any>, outputs: Record<string, any> }} meta
 * @param {Set<string>} initialOutputs
 */
function entryFileCosts(meta, initialOutputs) {
  /** @type {Map<string, number>} */
  const bundled = new Map();
  for (const [key, output] of Object.entries(meta.outputs)) {
    if (initialOutputs.has(key) || !key.endsWith('.js')) continue;
    for (const [file, m] of Object.entries(output.inputs ?? {})) {
      bundled.set(file, (bundled.get(file) ?? 0) + (m.bytesInOutput ?? 0));
    }
  }
  return Object.keys(meta.inputs)
    .filter(isEntryFile)
    .sort()
    .map((file) => {
      const { visited } = sourceClosure(meta, file);
      const bytes = [...visited].reduce((sum, f) => sum + (bundled.get(f) ?? 0), 0);
      return { file, bytes, heavy: heavyPackages(visited) };
    });
}

module.exports = { HEAVY_PACKAGES, entryFileCosts, lazyEntries, median, sourceClosure, staticOutputs };
