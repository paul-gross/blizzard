// @ts-check
/*
 * The disjoint-diffs sweep — the tooled half of `bzh:frontend-disjoint-diffs`, run inside
 * `web:structural-gate` (`structural-gate.js`'s `main` calls it like any other sweep).
 *
 * Two checks keep the `fleet` entry points a feature's only shared edit, one export line apiece:
 *
 * - **Unconsumed entry export.** Every export of an entry file (`fleet/src/entries/**`) needs a
 *   consumer outside `fleet`: `fleet` imports itself by file, so an entry serves only the apps.
 *   Consumption is judged per symbol: a file outside `projects/fleet/` imports a name that resolves,
 *   through any alias chain, to the same declaration, by whichever route (an entry, `fleet/shell`, a
 *   deep relative path). A re-export is not consumption, a spec counts as a consumer, and a namespace
 *   or dynamic `import()` of a module consumes all its exports. A name declared in the generated
 *   `lib/api/` is not judged.
 * - **Library aggregate.** A `public-api.ts` statement re-exports only from an entry file.
 *   `shell-api.ts` is not read: its deep named re-exports are what `bzh:frontend-eager-shell-entry`
 *   requires.
 *
 * Resolution reuses the placement sweep's compiler-API program. Run through
 * `npm run structural-gate`; this module only exports.
 */

const path = require('node:path');
const ts = require('typescript');
const { createProgram, walkTs, isUnder, toPosix } = require('./placement-sweep');

const ROOT = path.resolve(__dirname, '..');

const FLEET = 'projects/fleet';
const FLEET_SRC = `${FLEET}/src`;
const FLEET_LIB = `${FLEET_SRC}/lib`;
const ENTRIES = `${FLEET_SRC}/entries`;
const AGGREGATE = `${FLEET_SRC}/public-api.ts`;

/**
 * The symbol an import binding or entry export finally resolves to.
 *
 * @param {ts.TypeChecker} checker
 * @param {ts.Symbol} symbol
 */
function resolveAlias(checker, symbol) {
  return symbol.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(symbol) : symbol;
}

/**
 * Every declaration an import in `sf` consumes, recorded with the importing file. Only imports
 * consume: an `export … from` passes a name through and keeps nothing alive.
 *
 * @param {ts.TypeChecker} checker
 * @param {ts.SourceFile} sf
 * @param {Map<ts.Symbol, Set<string>>} consumers declaration -> files importing it
 */
function recordImports(checker, sf, consumers) {
  const file = toPosix(sf.fileName);
  /** @param {ts.Symbol | undefined} symbol */
  const add = (symbol) => {
    if (!symbol) return;
    const target = resolveAlias(checker, symbol);
    if (!consumers.has(target)) consumers.set(target, new Set());
    /** @type {Set<string>} */ (consumers.get(target)).add(file);
  };
  /** @param {ts.Expression} specifier */
  const addWholeModule = (specifier) => {
    const moduleSymbol = checker.getSymbolAtLocation(specifier);
    if (!moduleSymbol) return;
    for (const exported of checker.getExportsOfModule(moduleSymbol)) add(exported);
  };
  /** @param {ts.Node} node */
  const visit = (node) => {
    if (ts.isImportDeclaration(node) && ts.isStringLiteralLike(node.moduleSpecifier)) {
      const clause = node.importClause;
      if (clause?.name) add(checker.getSymbolAtLocation(clause.name));
      const bindings = clause?.namedBindings;
      if (bindings && ts.isNamespaceImport(bindings)) addWholeModule(node.moduleSpecifier);
      else if (bindings && ts.isNamedImports(bindings)) for (const el of bindings.elements) add(checker.getSymbolAtLocation(el.name));
    } else if (ts.isImportTypeNode(node) && node.qualifier) {
      add(checker.getSymbolAtLocation(ts.isIdentifier(node.qualifier) ? node.qualifier : node.qualifier.right));
    } else if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword &&
      node.arguments.length > 0 &&
      ts.isStringLiteralLike(node.arguments[0])
    ) {
      addWholeModule(node.arguments[0]);
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
}

/**
 * For each name an entry exports, the 1-based line of the statement that exports it.
 *
 * @param {ts.TypeChecker} checker
 * @param {ts.SourceFile} sf
 * @returns {Map<string, number>}
 */
function exportLines(checker, sf) {
  /** @type {Map<string, number>} */
  const lines = new Map();
  for (const stmt of sf.statements) {
    if (!ts.isExportDeclaration(stmt)) continue;
    const line = sf.getLineAndCharacterOfPosition(stmt.getStart()).line + 1;
    const clause = stmt.exportClause;
    if (clause && ts.isNamedExports(clause)) {
      for (const el of clause.elements) lines.set(el.name.text, line);
    } else if (clause && ts.isNamespaceExport(clause)) {
      lines.set(clause.name.text, line);
    } else if (stmt.moduleSpecifier) {
      const moduleSymbol = checker.getSymbolAtLocation(stmt.moduleSpecifier);
      for (const exported of moduleSymbol ? checker.getExportsOfModule(moduleSymbol) : []) {
        if (!lines.has(exported.name)) lines.set(exported.name, line);
      }
    }
  }
  return lines;
}

/**
 * Run the sweep over the tree at `root`.
 *
 * @param {{ root: string, memory?: Map<string, string> | null }} opts
 * @returns {{ unconsumed: { file: string, line: number, name: string }[], aggregate: { file: string, line: number, source: string }[] }}
 *   every entry export no file outside `fleet` imports, and every aggregate
 *   re-export that bypasses the entries
 */
function sweepDisjointDiffs({ root, memory = null }) {
  const abs = (/** @type {string} */ rel) => toPosix(path.join(root, rel));
  const rel = (/** @type {string} */ file) => path.relative(root, file);
  const fleet = abs(FLEET);
  const fleetApi = abs(`${FLEET_LIB}/api`);
  const entries = abs(ENTRIES);
  const files = memory ? [...memory.keys()] : walkTs(abs('projects'));
  const program = createProgram(root, files, memory);
  const checker = program.getTypeChecker();

  /** @type {Map<ts.Symbol, Set<string>>} */
  const consumers = new Map();
  for (const file of files) {
    const sf = program.getSourceFile(file);
    if (sf) recordImports(checker, sf, consumers);
  }

  /** @type {{ file: string, line: number, name: string }[]} */
  const unconsumed = [];
  for (const file of files.filter((f) => isUnder(f, entries)).sort()) {
    const sf = program.getSourceFile(file);
    const moduleSymbol = sf && checker.getSymbolAtLocation(sf);
    if (!sf || !moduleSymbol) continue;
    const lines = exportLines(checker, sf);
    for (const exported of checker.getExportsOfModule(moduleSymbol)) {
      const target = resolveAlias(checker, exported);
      const declared = target.declarations?.[0]?.getSourceFile().fileName;
      if (!declared || isUnder(toPosix(declared), fleetApi)) continue;
      const users = consumers.get(target);
      if (users && [...users].some((user) => !isUnder(user, fleet))) continue;
      unconsumed.push({ file: rel(file), line: lines.get(exported.name) ?? 1, name: exported.name });
    }
  }

  /** @type {{ file: string, line: number, source: string }[]} */
  const aggregate = [];
  const aggregateFile = abs(AGGREGATE);
  const aggregateSf = program.getSourceFile(aggregateFile);
  for (const stmt of aggregateSf?.statements ?? []) {
    if (!ts.isExportDeclaration(stmt) || !stmt.moduleSpecifier || !ts.isStringLiteralLike(stmt.moduleSpecifier)) continue;
    const moduleSymbol = checker.getSymbolAtLocation(stmt.moduleSpecifier);
    const target = moduleSymbol?.declarations?.[0]?.getSourceFile().fileName;
    if (target && isUnder(toPosix(target), entries)) continue;
    const line = /** @type {ts.SourceFile} */ (aggregateSf).getLineAndCharacterOfPosition(stmt.getStart()).line + 1;
    aggregate.push({ file: rel(aggregateFile), line, source: stmt.moduleSpecifier.text });
  }

  return { unconsumed, aggregate };
}

/**
 * Prove the sweep can still fail, before trusting it over the tree (`bzh:case-pins-its-own-name`):
 * an export nothing outside `fleet` imports, and an aggregate line bypassing the entries, must
 * each be caught; an export an app reaches through an entry or a deep relative path, from a spec,
 * by an `import()` type, or by a namespace or dynamic import must pass, and so must a generated name.
 */
function assertDisjointDiffsDetectorWorks() {
  const root = '/disjoint-fixture';
  const lib = `${root}/projects/fleet/src/lib`;
  const entries = `${root}/projects/fleet/src/entries`;
  const baseFiles = (/** @type {string} */ publicApi) => [
    [`${root}/projects/fleet/src/public-api.ts`, publicApi],
    [`${lib}/api/hub/index.ts`, 'export const unreadApi = 1;\n'],
  ];

  const main = new Map([
    ...baseFiles("export * from './entries/core';\nexport * from './entries/hub-api';\nexport { loose } from './lib/loose';\n"),
    [`${entries}/hub-api.ts`, "export { unreadApi } from '../lib/api/hub';\n"],
    [
      `${entries}/core.ts`,
      "export { usedByApp, unusedAnywhere, usedInsideFleetOnly } from '../lib/feat-a/a';\n" +
        "export { usedByDeepImport, usedBySpec, type UsedByImportType } from '../lib/feat-b/b';\n",
    ],
    [`${lib}/loose.ts`, 'export const loose = 1;\n'],
    [
      `${lib}/feat-a/a.ts`,
      'export const usedByApp = 1;\nexport const unusedAnywhere = 2;\nexport const usedInsideFleetOnly = 3;\n',
    ],
    [
      `${lib}/feat-b/c.ts`,
      "import { usedInsideFleetOnly } from '../feat-a/a';\nexport const inside = usedInsideFleetOnly;\n",
    ],
    [
      `${lib}/feat-b/b.ts`,
      'export const usedByDeepImport = 1;\nexport const usedBySpec = 2;\nexport type UsedByImportType = string;\n',
    ],
    [
      `${root}/projects/hub/src/deep.ts`,
      "import { usedByDeepImport } from '../../fleet/src/lib/feat-b/b';\nexport const deep = usedByDeepImport;\n",
    ],
    [
      `${root}/projects/hub/src/main.ts`,
      "import { usedByApp } from 'fleet/core';\nexport const used = usedByApp;\n" +
        "export let t: import('fleet/core').UsedByImportType;\n",
    ],
    [
      `${root}/projects/hub/src/main.spec.ts`,
      "import { usedBySpec } from 'fleet/core';\nexport const used = usedBySpec;\n",
    ],
  ]);
  const found = sweepDisjointDiffs({ root, memory: main });
  const names = found.unconsumed.map((u) => u.name).sort().join(',');
  if (names !== 'unusedAnywhere,usedInsideFleetOnly') {
    throw new Error(
      `disjoint-diffs self-test: expected exactly the unconsumed and fleet-only exports to be caught, saw [${names}]`,
    );
  }
  if (found.aggregate.map((r) => r.source).join(',') !== './lib/loose') {
    throw new Error('disjoint-diffs self-test: expected only the aggregate line bypassing the entries to be caught');
  }

  // A namespace or dynamic import consumes every export of its module; an unrelated import consumes none.
  const wholeModule = (/** @type {string} */ consumer) =>
    sweepDisjointDiffs({
      root,
      memory: new Map([
        ...baseFiles("export * from './entries/core';\n"),
        [`${entries}/core.ts`, "export { thing } from '../lib/feat/thing';\n"],
        [`${lib}/feat/thing.ts`, 'export const thing = 1;\n'],
        [`${root}/projects/hub/src/main.ts`, consumer],
      ]),
    }).unconsumed.length;
  if (wholeModule("import * as f from 'fleet/core';\nexport const all = f;\n") !== 0) {
    throw new Error('disjoint-diffs self-test: a namespace import of an entry was not read as consuming its exports');
  }
  if (wholeModule("export const all = import('fleet/core');\n") !== 0) {
    throw new Error('disjoint-diffs self-test: a dynamic import of an entry was not read as consuming its exports');
  }
  if (wholeModule('export const all = 1;\n') !== 1) {
    throw new Error('disjoint-diffs self-test: an export with no importer at all was not caught');
  }
}

/**
 * The sweep over the real tree.
 *
 * @returns {string[]} violation lines, empty when clean
 */
function disjointDiffsViolations() {
  const { unconsumed, aggregate } = sweepDisjointDiffs({ root: ROOT });
  /** @type {string[]} */
  const lines = [];
  for (const u of unconsumed) {
    lines.push(`  ${u.file}:${u.line}: ${u.name} — no consumer outside fleet`);
  }
  for (const r of aggregate) {
    lines.push(`  ${r.file}:${r.line}: re-exports ${r.source} directly — export it through an entry`);
  }
  return lines;
}

module.exports = { assertDisjointDiffsDetectorWorks, disjointDiffsViolations, sweepDisjointDiffs };
