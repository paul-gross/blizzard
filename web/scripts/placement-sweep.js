// @ts-check
/*
 * The placement sweep — the tooled half of `bzh:frontend-placement`, run inside
 * `web:structural-gate` (`structural-gate.js`'s `main` calls it like any other sweep).
 *
 * The placement unit is each direct child of `fleet/src/lib/`, a feature folder or a
 * top-level module. `fleet` holds only what both apps reach, so a unit must be reached by
 * the non-spec code of **both** `hub` and `runner`. The sweep fails naming the unit and the single
 * app that reaches it, or naming no app when the unit is dead. It also fails any `fleet`
 * file that imports from an app project — `fleet` never depends on its consumers.
 *
 * Reach is resolved **to the symbol**, not the file, with the TypeScript compiler: an
 * imported name is followed through the `fleet` / `fleet/shell` barrels and every
 * sub-barrel to the file that declares it, and that file's own imports are followed the
 * same way. Resolving to the file instead would mark every sub-barrel re-exported from
 * `public-api.ts` as reached and the sweep could never fail.
 *
 * Mixed folders (`kit/`, `viewport/`, `sse/`, `chunk-detail/`) pass at the folder level:
 * one shared file reached by both apps carries the unit.
 *
 * Run through `npm run structural-gate`; this module only exports.
 */

const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const ROOT = path.resolve(__dirname, '..');

/** The app projects, each with the source roots whose reach counts as that app's. */
const APPS = [
  { name: 'hub', roots: ['projects/hub/src'] },
  { name: 'runner', roots: ['projects/runner/src'] },
];

/** Projects a `fleet` file must never import from. */
const APP_PROJECTS = ['projects/hub', 'projects/runner'];

const FLEET_LIB = 'projects/fleet/src/lib';

/**
 * Units exempt from the reach rule, each with a reason:
 *
 * - `testing/` is spec support behind its own `fleet/testing` entry; only specs reach it,
 *   and spec reach does not count.
 * - `format/` is a declaration-free re-export barrel over top-level modules; reach lands on
 *   those modules, which are units of their own, never on the barrel.
 */
const PLACEMENT_EXEMPT_UNITS = ['testing', 'format'];

/** @param {string} p */
const toPosix = (p) => p.split(path.sep).join('/');

/** @param {string} file */
const isSpec = (file) => file.endsWith('.spec.ts');

/**
 * @param {string} file absolute
 * @param {string} dir absolute
 */
const isUnder = (file, dir) => file === dir || file.startsWith(dir + '/');

/** Every `.ts` file below `dir`, absolute, forward-slashed.
 * @param {string} dir
 * @returns {string[]}
 */
function walkTs(dir) {
  /** @type {string[]} */
  const out = [];
  if (!fs.existsSync(dir)) return out;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walkTs(full));
    else if (entry.isFile() && entry.name.endsWith('.ts') && !entry.name.endsWith('.d.ts')) out.push(toPosix(full));
  }
  return out;
}

/**
 * A program over `rootNames`, resolving only modules inside `root` — third-party imports
 * stay unresolved, since placement never reaches past the workspace's own projects.
 *
 * @param {string} root absolute web root
 * @param {readonly string[]} rootNames
 * @param {Map<string, string> | null} memory in-memory sources, or `null` to read disk
 */
function createProgram(root, rootNames, memory) {
  /** @type {ts.CompilerOptions} */
  const options = {
    baseUrl: root,
    paths: {
      fleet: ['./projects/fleet/src/public-api.ts'],
      'fleet/shell': ['./projects/fleet/src/shell-api.ts'],
      'fleet/testing': ['./projects/fleet/src/lib/testing/public-api.ts'],
    },
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.Preserve,
    moduleResolution: ts.ModuleResolutionKind.Bundler,
    noLib: true,
    types: [],
    experimentalDecorators: true,
  };
  const host = ts.createCompilerHost(options, true);
  if (memory) {
    host.fileExists = (f) => memory.has(f);
    host.readFile = (f) => memory.get(f);
    host.directoryExists = (d) => [...memory.keys()].some((f) => f.startsWith(d.endsWith('/') ? d : d + '/'));
    host.getDirectories = () => [];
    host.getSourceFile = (f, languageVersion) => {
      const text = memory.get(f);
      return text === undefined ? undefined : ts.createSourceFile(f, text, languageVersion, true);
    };
  }
  const projects = toPosix(path.join(root, 'projects'));
  host.resolveModuleNameLiterals = (literals, containingFile) =>
    literals.map((literal) => {
      const { resolvedModule } = ts.resolveModuleName(literal.text, containingFile, options, host);
      if (!resolvedModule || !isUnder(toPosix(resolvedModule.resolvedFileName), projects)) {
        return { resolvedModule: undefined };
      }
      return { resolvedModule };
    });
  return ts.createProgram({ rootNames: [...rootNames], options, host });
}

/**
 * The module specifiers a source file imports: static imports and re-exports, plus
 * dynamic `import()` calls anywhere in the file.
 *
 * @param {ts.SourceFile} sf
 * @returns {ts.StringLiteralLike[]}
 */
function moduleSpecifiers(sf) {
  /** @type {ts.StringLiteralLike[]} */
  const out = [];
  /** @param {ts.Node} node */
  const visit = (node) => {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) {
      if (ts.isStringLiteralLike(node.moduleSpecifier)) out.push(node.moduleSpecifier);
    } else if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword &&
      node.arguments.length > 0 &&
      ts.isStringLiteralLike(node.arguments[0])
    ) {
      out.push(node.arguments[0]);
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return out;
}

/**
 * The files declaring each name `sf` imports, followed through every alias (barrel
 * re-export) to the declaration itself. Re-exports are not reach: a barrel's `export … from`
 * passes a name through, and the importer of that name is what reaches its declaration.
 *
 * @param {ts.TypeChecker} checker
 * @param {ts.SourceFile} sf
 * @returns {Set<string>}
 */
function declarationFilesImportedBy(checker, sf) {
  /** @type {Set<string>} */
  const files = new Set();
  /** @param {ts.Symbol | undefined} symbol */
  const addSymbol = (symbol) => {
    if (!symbol) return;
    const target = symbol.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(symbol) : symbol;
    for (const decl of target.declarations ?? []) files.add(toPosix(decl.getSourceFile().fileName));
  };
  /** @param {ts.Expression} specifier */
  const addWholeModule = (specifier) => {
    const moduleSymbol = checker.getSymbolAtLocation(specifier);
    if (!moduleSymbol) return;
    for (const decl of moduleSymbol.declarations ?? []) files.add(toPosix(decl.getSourceFile().fileName));
    for (const exported of checker.getExportsOfModule(moduleSymbol)) addSymbol(exported);
  };

  for (const specifier of moduleSpecifiers(sf)) {
    const parent = specifier.parent;
    if (ts.isImportDeclaration(parent)) {
      const clause = parent.importClause;
      if (!clause) {
        addWholeModule(specifier); // side-effect import
        continue;
      }
      if (clause.name) addSymbol(checker.getSymbolAtLocation(clause.name));
      const bindings = clause.namedBindings;
      if (bindings && ts.isNamespaceImport(bindings)) addWholeModule(specifier);
      else if (bindings && ts.isNamedImports(bindings)) for (const el of bindings.elements) addSymbol(checker.getSymbolAtLocation(el.name));
    } else if (ts.isCallExpression(parent)) {
      addWholeModule(specifier); // dynamic import: any export may be read off the module
    }
  }
  return files;
}

/**
 * The unit a `fleet/src/lib/` file belongs to — its direct child of `lib/` — or `null`
 * when the file is outside `lib/`.
 *
 * @param {string} file absolute
 * @param {string} fleetLib absolute
 */
function unitOf(file, fleetLib) {
  if (!isUnder(file, fleetLib)) return null;
  return file.slice(fleetLib.length + 1).split('/')[0];
}

/**
 * Run the sweep over the tree at `root`.
 *
 * @param {{ root: string, memory?: Map<string, string> | null }} opts
 * @returns {{ units: { unit: string, reach: string[] }[], appImports: { file: string, target: string }[] }}
 *   every non-exempt unit reached by fewer than both apps, with the apps that do reach
 *   it, and every `fleet` file importing an app project
 */
function sweepPlacement({ root, memory = null }) {
  const abs = (/** @type {string} */ rel) => toPosix(path.join(root, rel));
  const fleetLib = abs(FLEET_LIB);
  const files = memory ? [...memory.keys()] : walkTs(abs('projects'));
  const program = createProgram(root, files, memory);
  const checker = program.getTypeChecker();

  /** @type {Map<string, Set<string>>} unit -> apps reaching it */
  const reachByUnit = new Map();
  const units = new Set();
  for (const file of files) {
    const unit = unitOf(file, fleetLib);
    if (unit !== null && !isSpec(file)) units.add(unit);
  }

  for (const app of APPS) {
    const appRoots = app.roots.map(abs);
    const queue = files.filter((f) => !isSpec(f) && appRoots.some((r) => isUnder(f, r)));
    const seen = new Set(queue);
    while (queue.length > 0) {
      const file = /** @type {string} */ (queue.pop());
      const sf = program.getSourceFile(file);
      if (!sf) continue;
      for (const reached of declarationFilesImportedBy(checker, sf)) {
        if (seen.has(reached) || isSpec(reached)) continue;
        const inFleet = isUnder(reached, fleetLib);
        const inOwnApp = appRoots.some((r) => isUnder(reached, r));
        if (!inFleet && !inOwnApp) continue;
        seen.add(reached);
        queue.push(reached);
        const unit = unitOf(reached, fleetLib);
        if (unit !== null) {
          if (!reachByUnit.has(unit)) reachByUnit.set(unit, new Set());
          /** @type {Set<string>} */ (reachByUnit.get(unit)).add(app.name);
        }
      }
    }
  }

  const unitFindings = [...units]
    .filter((unit) => !PLACEMENT_EXEMPT_UNITS.includes(unit))
    .map((unit) => ({ unit, reach: [...(reachByUnit.get(unit) ?? [])].sort() }))
    .filter((f) => f.reach.length < APPS.length)
    .sort((a, b) => a.unit.localeCompare(b.unit));

  /** @type {{ file: string, target: string }[]} */
  const appImports = [];
  const appProjects = APP_PROJECTS.map(abs);
  const fleetProject = abs('projects/fleet');
  for (const file of files) {
    if (!isUnder(file, fleetProject)) continue;
    const sf = program.getSourceFile(file);
    if (!sf) continue;
    for (const specifier of moduleSpecifiers(sf)) {
      const moduleSymbol = checker.getSymbolAtLocation(specifier);
      const target = moduleSymbol?.declarations?.[0]?.getSourceFile().fileName;
      if (target && appProjects.some((p) => isUnder(toPosix(target), p))) {
        appImports.push({ file: path.relative(root, file), target: path.relative(root, target) });
      }
    }
  }

  return { units: unitFindings, appImports };
}

/**
 * Prove the placement detector can still fail, before trusting it over the tree
 * (`bzh:case-pins-its-own-name`): a unit only one app reaches and a `fleet` file importing
 * an app must each be caught, and a unit both apps reach — through the `fleet` barrel and a
 * sub-barrel, the shape a file-level resolver gets wrong — must pass.
 */
function assertPlacementDetectorWorks() {
  const root = '/placement-fixture';
  const memory = new Map([
    [`${root}/projects/fleet/src/public-api.ts`, "export * from './lib/shared';\nexport * from './lib/hub-only';\n"],
    [`${root}/projects/fleet/src/shell-api.ts`, ''],
    [`${root}/projects/fleet/src/lib/shared/index.ts`, "export { sharedThing } from './shared-thing';\n"],
    [`${root}/projects/fleet/src/lib/shared/shared-thing.ts`, 'export const sharedThing = 1;\n'],
    [`${root}/projects/fleet/src/lib/hub-only/index.ts`, "export { hubThing } from './hub-thing';\n"],
    [
      `${root}/projects/fleet/src/lib/hub-only/hub-thing.ts`,
      "import { appThing } from '../../../../hub/src/app/app-thing';\nexport const hubThing = appThing;\n",
    ],
    [`${root}/projects/hub/src/app/app-thing.ts`, 'export const appThing = 2;\n'],
    [
      `${root}/projects/hub/src/main.ts`,
      "import { sharedThing, hubThing } from 'fleet';\nexport const used = [sharedThing, hubThing];\n",
    ],
    [`${root}/projects/runner/src/main.ts`, "import { sharedThing } from 'fleet';\nexport const used = sharedThing;\n"],
    [
      `${root}/projects/runner/src/main.spec.ts`,
      "import { hubThing } from 'fleet';\nexport const specOnly = hubThing;\n",
    ],
  ]);
  const { units, appImports } = sweepPlacement({ root, memory });

  const hubOnly = units.find((u) => u.unit === 'hub-only');
  if (!hubOnly || hubOnly.reach.join(',') !== 'hub') {
    throw new Error('placement self-test: a unit only the hub reaches was not caught as hub-only');
  }
  if (units.some((u) => u.unit === 'shared')) {
    throw new Error('placement self-test: a unit both apps reach was flagged');
  }
  if (!appImports.some((i) => i.file === path.join('projects', 'fleet', 'src', 'lib', 'hub-only', 'hub-thing.ts'))) {
    throw new Error('placement self-test: a fleet file importing an app project was not caught');
  }
}

/**
 * The sweep over the real tree.
 *
 * @returns {string[]} violation lines, empty when clean
 */
function placementViolations() {
  const { units, appImports } = sweepPlacement({ root: ROOT });
  /** @type {string[]} */
  const lines = [];

  for (const f of units) {
    lines.push(
      f.reach.length === 0
        ? `  fleet/src/lib/${f.unit}: reached by no app (dead)`
        : `  fleet/src/lib/${f.unit}: reached only by ${f.reach.join(', ')}`,
    );
  }
  for (const i of appImports) lines.push(`  ${i.file}: imports app code from ${i.target}`);
  return lines;
}

module.exports = { assertPlacementDetectorWorks, placementViolations, sweepPlacement };
