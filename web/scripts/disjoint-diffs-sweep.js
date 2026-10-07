// @ts-check
/*
 * The disjoint-diffs sweep — the tooled half of `bzh:frontend-disjoint-diffs`, run inside
 * `web:structural-gate` (`structural-gate.js`'s `main` calls it like any other sweep).
 *
 * Two checks keep the `fleet` root barrel thin, so two features landing in parallel touch
 * different sub-barrels instead of colliding on `public-api.ts`:
 *
 * - **Unconsumed sub-barrel export.** Every export of a fleet sub-barrel — each
 *   `fleet/src/lib/**` `index.ts` outside `lib/api/` — needs a consumer outside that sub-barrel's own
 *   directory. Consumption is judged per symbol: a file outside the directory imports a name that
 *   resolves, through any alias chain, to the same declaration, by whichever route (`fleet`,
 *   `fleet/shell`, a sub-barrel, a deep relative path). A re-export is not consumption, a spec
 *   counts as a consumer, and a namespace or dynamic `import()` of a module consumes all its exports.
 * - **Root direct export.** A `public-api.ts` statement re-exports only from a sub-barrel, save the
 *   sources in `ROOT_DIRECT_EXPORT_ALLOWED`. `shell-api.ts` is not read: its deep named
 *   re-exports are what `bzh:frontend-eager-shell-entry` requires.
 *
 * Resolution reuses the placement sweep's compiler-API program. Run through
 * `npm run structural-gate`; this module only exports.
 */

const path = require('node:path');
const ts = require('typescript');
const { createProgram, walkTs, isUnder, toPosix } = require('./placement-sweep');

const ROOT = path.resolve(__dirname, '..');

const FLEET_SRC = 'projects/fleet/src';
const FLEET_LIB = `${FLEET_SRC}/lib`;
const ROOT_BARREL = `${FLEET_SRC}/public-api.ts`;

/**
 * Sources the root barrel may re-export directly (paths under `fleet/src/lib/`), each with a reason:
 *
 * - `api/` is the generated client surface; it has no feature owner and is regenerated, not edited.
 * - `core/query-keys` is the query-key registry; it has no single feature owner.
 */
const ROOT_DIRECT_EXPORT_ALLOWED = [
  { prefix: 'api/', reason: 'the generated client surface has no feature owner' },
  { prefix: 'core/query-keys', reason: 'the query-key registry has no single feature owner' },
];

/**
 * Is `file` a fleet sub-barrel, an `index.ts` under `lib/` outside the generated `lib/api/`?
 *
 * @param {string} file absolute
 * @param {string} fleetLib absolute
 */
function isSubBarrel(file, fleetLib) {
  return isUnder(file, fleetLib) && path.posix.basename(file) === 'index.ts' && !isUnder(file, `${fleetLib}/api`);
}

/**
 * The symbol an import binding or barrel export finally resolves to.
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
 * For each name a barrel exports, the 1-based line of the statement that exports it.
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
 * @returns {{ unconsumed: { file: string, line: number, name: string }[], rootDirect: { file: string, line: number, source: string }[] }}
 *   every sub-barrel export no file outside the barrel's directory imports, and every root
 *   re-export that bypasses a sub-barrel
 */
function sweepDisjointDiffs({ root, memory = null }) {
  const abs = (/** @type {string} */ rel) => toPosix(path.join(root, rel));
  const rel = (/** @type {string} */ file) => path.relative(root, file);
  const fleetLib = abs(FLEET_LIB);
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
  for (const file of files.filter((f) => isSubBarrel(f, fleetLib)).sort()) {
    const sf = program.getSourceFile(file);
    const moduleSymbol = sf && checker.getSymbolAtLocation(sf);
    if (!sf || !moduleSymbol) continue;
    const dir = path.posix.dirname(file);
    const lines = exportLines(checker, sf);
    for (const exported of checker.getExportsOfModule(moduleSymbol)) {
      const users = consumers.get(resolveAlias(checker, exported));
      if (users && [...users].some((user) => !isUnder(user, dir))) continue;
      unconsumed.push({ file: rel(file), line: lines.get(exported.name) ?? 1, name: exported.name });
    }
  }

  /** @type {{ file: string, line: number, source: string }[]} */
  const rootDirect = [];
  const rootFile = abs(ROOT_BARREL);
  const rootSf = program.getSourceFile(rootFile);
  for (const stmt of rootSf?.statements ?? []) {
    if (!ts.isExportDeclaration(stmt) || !stmt.moduleSpecifier || !ts.isStringLiteralLike(stmt.moduleSpecifier)) continue;
    const moduleSymbol = checker.getSymbolAtLocation(stmt.moduleSpecifier);
    const target = moduleSymbol?.declarations?.[0]?.getSourceFile().fileName;
    const inLib = target && isUnder(toPosix(target), fleetLib) ? toPosix(target).slice(fleetLib.length + 1) : null;
    if (inLib !== null && (isSubBarrel(toPosix(target ?? ''), fleetLib) || ROOT_DIRECT_EXPORT_ALLOWED.some((a) => inLib.startsWith(a.prefix)))) {
      continue;
    }
    const line = /** @type {ts.SourceFile} */ (rootSf).getLineAndCharacterOfPosition(stmt.getStart()).line + 1;
    rootDirect.push({ file: rel(rootFile), line, source: stmt.moduleSpecifier.text });
  }

  return { unconsumed, rootDirect };
}

/**
 * Prove the sweep can still fail, before trusting it over the tree (`bzh:case-pins-its-own-name`):
 * an export nothing outside its directory imports, and a root line bypassing a sub-barrel, must
 * each be caught; an export reached through `fleet` or a deep relative path, from an outside spec,
 * or by a namespace or dynamic import must pass, and so must the root's allow-listed sources.
 */
function assertDisjointDiffsDetectorWorks() {
  const root = '/disjoint-fixture';
  const lib = `${root}/projects/fleet/src/lib`;
  const baseFiles = (/** @type {string} */ publicApi) => [
    [`${root}/projects/fleet/src/public-api.ts`, publicApi],
    [`${lib}/core/query-keys.ts`, 'export const someKey = 1;\n'],
    [`${lib}/api/hub/index.ts`, 'export const unreadApi = 1;\n'],
  ];

  const main = new Map([
    ...baseFiles(
      "export * from './lib/feat-a';\nexport * from './lib/feat-b';\n" +
        "export { loose } from './lib/loose';\nexport { someKey } from './lib/core/query-keys';\n" +
        "export * as hubApi from './lib/api/hub';\n",
    ),
    [`${lib}/loose.ts`, 'export const loose = 1;\n'],
    [`${lib}/feat-a/index.ts`, "export { usedByApp, unusedAnywhere, usedInsideOnly } from './a';\n"],
    [
      `${lib}/feat-a/a.ts`,
      'export const usedByApp = 1;\nexport const unusedAnywhere = 2;\nexport const usedInsideOnly = 3;\n',
    ],
    [`${lib}/feat-a/b.ts`, "import { usedInsideOnly } from './a';\nexport const inside = usedInsideOnly;\n"],
    [`${lib}/feat-b/index.ts`, "export { usedByDeepImport, usedBySpec } from './b';\n"],
    [`${lib}/feat-b/b.ts`, 'export const usedByDeepImport = 1;\nexport const usedBySpec = 2;\n'],
    [
      `${lib}/feat-a/c.ts`,
      "import { usedByDeepImport } from '../feat-b/b';\nexport const deep = usedByDeepImport;\n",
    ],
    [
      `${root}/projects/hub/src/main.ts`,
      "import { usedByApp } from 'fleet';\nexport const used = usedByApp;\n",
    ],
    [
      `${root}/projects/hub/src/main.spec.ts`,
      "import { usedBySpec } from 'fleet';\nexport const used = usedBySpec;\n",
    ],
  ]);
  const found = sweepDisjointDiffs({ root, memory: main });
  const names = found.unconsumed.map((u) => u.name).sort().join(',');
  if (names !== 'unusedAnywhere,usedInsideOnly') {
    throw new Error(
      `disjoint-diffs self-test: expected exactly the unconsumed and inside-only exports to be caught, saw [${names}]`,
    );
  }
  if (found.rootDirect.map((r) => r.source).join(',') !== './lib/loose') {
    throw new Error(
      'disjoint-diffs self-test: expected only the direct root re-export to be caught — sub-barrel, query-keys, and api lines pass',
    );
  }

  // A namespace or dynamic import consumes every export of its module; an unrelated import consumes none.
  const wholeModule = (/** @type {string} */ consumer) =>
    sweepDisjointDiffs({
      root,
      memory: new Map([
        ...baseFiles("export * from './lib/feat';\n"),
        [`${lib}/feat/index.ts`, "export { thing } from './thing';\n"],
        [`${lib}/feat/thing.ts`, 'export const thing = 1;\n'],
        [`${root}/projects/hub/src/main.ts`, consumer],
      ]),
    }).unconsumed.length;
  if (wholeModule("import * as f from 'fleet';\nexport const all = f;\n") !== 0) {
    throw new Error('disjoint-diffs self-test: a namespace import of the package entry was not read as consuming its exports');
  }
  if (wholeModule("export const all = import('fleet');\n") !== 0) {
    throw new Error('disjoint-diffs self-test: a dynamic import of the package entry was not read as consuming its exports');
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
  const { unconsumed, rootDirect } = sweepDisjointDiffs({ root: ROOT });
  /** @type {string[]} */
  const lines = [];
  for (const u of unconsumed) {
    lines.push(`  ${u.file}:${u.line}: ${u.name} — no consumer outside its sub-barrel's directory`);
  }
  for (const r of rootDirect) {
    lines.push(`  ${r.file}:${r.line}: re-exports ${r.source} directly — export it through a sub-barrel`);
  }
  return lines;
}

module.exports = { assertDisjointDiffsDetectorWorks, disjointDiffsViolations, sweepDisjointDiffs };
