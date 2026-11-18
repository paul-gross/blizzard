// @ts-check
/*
 * The domain-boundary sweeps — the frontend reads the hub and runner wire only at its boundary: the
 * generated clients under `fleet/src/lib/api/`, the data-access and translator files beside each
 * feature, and the test-only transport. Run inside `web:structural-gate` (`structural-gate.js`'s `main`
 * calls them like any other sweep).
 *
 * The symbol-origin sweep: every identifier a file references is resolved through the checker's full
 * alias chain — named, renamed, and default imports, `ns.Member` value and qualified-type access, and
 * `import("…").T` type nodes — to its declaration. A file consumes the wire when that declaration sits
 * in `fleet/src/lib/api/{hub,runner}/types.gen.ts`; the declaration's origin is judged, not the import's
 * source or spelling, so a domain type may share a generated type's name. A re-export statement passes
 * a name through and is not consumption: the barrel is never judged, its importer is. Consumption is
 * permitted under `fleet/src/lib/api/` and `fleet/src/lib/testing/transport/`, and in `*.query.ts`,
 * `*.mutations.ts`, and `*.translate.ts` files and their own focused specs; every other `.ts` under
 * `projects/` (`.storybook/` included) is judged. A unit the caller exempts passes, and an exempt unit
 * that no longer consumes the wire outside a strict scope is reported stale. Strict scopes cannot be
 * excused by a parent unit's exemption. Separately, a production file or a story whose
 * consumed symbol is declared under `testing/transport/` reaches the test transport; specs and test
 * helpers may.
 *
 * The backend-citation sweep: hand-written TS never cites a backend Python module — a `.py`
 * file or a dotted `blizzard.<package>…` path — in a comment or a string. The wire contract is the generated client and its types, not the Python
 * module that happens to serve it today; a citation rots silently when the backend moves.
 * Scope is every `.ts` under `projects/` except specs and the generated `fleet/src/lib/api/`.
 *
 * The client-call placement sweep: a generated client function (every `export const` in
 * `fleet/src/lib/api/{hub,runner}/sdk.gen.ts`, read at run time) is named only inside a
 * `*.query.ts` or `*.mutations.ts` file. That is the whole guarantee: the call sits in a
 * data-access file, not necessarily inside a query or mutation hook — a plain function a
 * data-access file exports still wraps the call where the sweep allows it. Comments and
 * `export … from` re-export statements are stripped first — a barrel re-exports a function, it
 * does not import or call it — except a renaming re-export (`export { fn as alias } from …`),
 * which would hide the name behind one the sweep cannot see. Then any whole-word occurrence of
 * a function name fails, catching both a named import and a `runnerApi.<name>` namespace access.
 *
 * The backend-citation and client-call placement sweeps have no exemption list.
 *
 * Run through `npm run structural-gate`; this module only exports.
 */

const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { createProgram } = require("./placement-sweep");

const ROOT = path.resolve(__dirname, "..");
const PROJECTS_DIR = path.join(ROOT, "projects");

/** The generated clients, relative to `projects/`; nothing under it is hand-written. */
const GENERATED_API_DIR = "fleet/src/lib/api/";

/** The generated SDK modules whose `export const`s are the client functions. */
const SDK_FILES = [
  "fleet/src/lib/api/hub/sdk.gen.ts",
  "fleet/src/lib/api/runner/sdk.gen.ts",
];

/** The backend's top-level Python packages — a dotted module path cites the backend only
 * under one of these, so a dotted storage key such as `blizzard.viewport.override` is not one. */
const BACKEND_PACKAGES = ["auth_core", "cli", "foundation", "hub", "runner", "tools", "wire"];

/** A backend Python module named anywhere in a source: a path or bare module ending in `.py`,
 * or a dotted `blizzard.<package>…` module path. A dotted path stops at a dot that ends a
 * sentence, and never ends inside a longer word or a hyphenated key. */
const BACKEND_CITATION = new RegExp(
  String.raw`[\w./-]*\w\.py\b|\bblizzard\.(?:${BACKEND_PACKAGES.join("|")})(?:\.[A-Za-z_]\w*)*(?![\w-]|\.\w)`,
  "g",
);

/** @param {string} p */
const toPosix = (p) => p.split(path.sep).join("/");

/** Every `.ts` file under `dir`, as `projects/`-relative posix paths.
 * @param {string} dir absolute
 * @returns {string[]}
 */
function walkTs(dir) {
  /** @type {string[]} */
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules") continue;
      out.push(...walkTs(full));
    } else if (entry.name.endsWith(".ts")) {
      out.push(full);
    }
  }
  return out;
}

/** 1-based line of `index` in `source`.
 * @param {string} source
 * @param {number} index
 */
function lineOf(source, index) {
  let line = 1;
  for (let i = 0; i < index; i++) if (source.charCodeAt(i) === 10) line++;
  return line;
}

/** @param {string} rel `projects/`-relative posix path */
function inBackendCitationScope(rel) {
  return (
    rel.endsWith(".ts") &&
    !rel.endsWith(".spec.ts") &&
    !rel.endsWith(".stories.ts") &&
    !rel.startsWith(GENERATED_API_DIR)
  );
}

/** @param {string} rel `projects/`-relative posix path */
function inClientCallScope(rel) {
  return (
    inBackendCitationScope(rel) &&
    !rel.endsWith(".query.ts") &&
    !rel.endsWith(".mutations.ts")
  );
}

/** Every backend `.py` citation in `source`, with its line.
 * @param {string} source
 * @returns {{ line: number, match: string }[]}
 */
function backendCitations(source) {
  BACKEND_CITATION.lastIndex = 0;
  return [...source.matchAll(BACKEND_CITATION)].map((m) => ({
    line: lineOf(source, m.index ?? 0),
    match: m[0],
  }));
}

/** Prove the backend-citation sweep catches a citation in every position and scopes out
 * specs and the generated clients (`bzh:case-pins-its-own-name`). */
function assertBackendCitationDetectorWorks() {
  const hits = [
    [
      "// mirrors src/blizzard/hub/routes/chunks.py",
      "src/blizzard/hub/routes/chunks.py",
    ],
    ["/** see `models.py` */", "models.py"],
    ["const origin = 'blizzard/runner/app.py';", "blizzard/runner/app.py"],
    ["// the _wire_shapes.py model", "_wire_shapes.py"],
    ["// proxied by `blizzard.runner.api.chunk_detail`", "blizzard.runner.api.chunk_detail"],
    ["// see blizzard.hub.config.", "blizzard.hub.config"],
    ["/** mirrors blizzard.wire.chunk.ChunkDetail */", "blizzard.wire.chunk.ChunkDetail"],
    ["// the blizzard.wire package", "blizzard.wire"],
  ];
  for (const [source, expected] of hits) {
    const found = backendCitations(source).map((c) => c.match);
    if (!found.includes(expected)) {
      throw new Error(
        `backend-citation sweep missed \`${expected}\` in \`${source}\``,
      );
    }
  }
  if (
    backendCitations('const a = 1;\n// x\nconst b = "app.py";')[0]?.line !== 3
  ) {
    throw new Error("backend-citation sweep reported the wrong line");
  }
  for (const source of [
    "const happy = 1;",
    "import x from './copy.pyramid';",
    "a.pyc",
    "numpy",
    "mypy.ini",
    "const KEY = 'blizzard.viewport.override';",
    "const KEY = 'blizzard.runner.session-renewal-attempted';",
    "// the blizzard.hubris module",
  ]) {
    if (backendCitations(source).length > 0) {
      throw new Error(
        `backend-citation sweep false-positived on \`${source}\``,
      );
    }
  }
  if (!inBackendCitationScope("hub/src/app/board/board.ts")) {
    throw new Error("backend-citation sweep scoped out a hand-written file");
  }
  for (const rel of [
    "hub/src/app/board/board.spec.ts",
    "fleet/src/lib/kit/kit-button.stories.ts",
    "fleet/src/lib/api/hub/types.gen.ts",
  ]) {
    if (inBackendCitationScope(rel))
      throw new Error(`backend-citation sweep scoped in \`${rel}\``);
  }
}

/** Backend `.py` citations under `projects/`, one report line each.
 * @param {string} [projectsDir] absolute
 * @returns {string[]}
 */
function backendCitationViolations(projectsDir = PROJECTS_DIR) {
  /** @type {string[]} */
  const lines = [];
  for (const file of walkTs(projectsDir)) {
    const rel = toPosix(path.relative(projectsDir, file));
    if (!inBackendCitationScope(rel)) continue;
    for (const c of backendCitations(fs.readFileSync(file, "utf8"))) {
      lines.push(`  ${rel}:${c.line}: ${c.match}`);
    }
  }
  return lines;
}

/** The generated client function names: every `export const` in the SDK modules.
 * @param {string} [projectsDir] absolute
 * @returns {string[]}
 */
function clientFunctionNames(projectsDir = PROJECTS_DIR) {
  /** @type {string[]} */
  const names = [];
  for (const rel of SDK_FILES) {
    const source = fs.readFileSync(path.join(projectsDir, rel), "utf8");
    for (const m of source.matchAll(/^export const (\w+)/gm)) names.push(m[1]);
  }
  if (names.length === 0) {
    throw new Error(
      `client-call placement sweep found no client functions in ${SDK_FILES.join(", ")}`,
    );
  }
  return names;
}

/** Whether an `export … from` statement re-exports any name under another name.
 * @param {ts.ExportDeclaration} node
 */
function renamesAnExport(node) {
  const clause = node.exportClause;
  return !!clause && ts.isNamedExports(clause) && clause.elements.some((e) => e.propertyName);
}

/** `source` with every comment and every non-renaming `export … from` re-export statement blanked to
 * spaces, newlines kept so offsets and line numbers still hold.
 * @param {string} source
 */
function stripCommentsAndReExports(source) {
  const sf = ts.createSourceFile(
    "sweep.ts",
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TS,
  );
  /** @type {[number, number][]} */
  const ranges = [];
  /** @param {ts.Node} node */
  const visit = (node) => {
    if (
      node.kind >= ts.SyntaxKind.FirstJSDocNode &&
      node.kind <= ts.SyntaxKind.LastJSDocNode
    )
      return;
    if (ts.isExportDeclaration(node) && node.moduleSpecifier && !renamesAnExport(node)) {
      ranges.push([node.getStart(sf), node.end]);
    }
    const children = node.getChildren(sf);
    if (children.length === 0) {
      for (const r of ts.getLeadingCommentRanges(source, node.pos) ?? [])
        ranges.push([r.pos, r.end]);
      for (const r of ts.getTrailingCommentRanges(source, node.end) ?? [])
        ranges.push([r.pos, r.end]);
      return;
    }
    for (const child of children) visit(child);
  };
  visit(sf);
  const chars = source.split("");
  for (const [start, end] of ranges) {
    for (let i = start; i < end; i++)
      if (chars[i] !== "\n" && chars[i] !== "\r") chars[i] = " ";
  }
  return chars.join("");
}

/** Every whole-word client function name `source` still names once comments and re-exports
 * are stripped, with its line.
 * @param {string} source
 * @param {string[]} names
 * @returns {{ line: number, name: string }[]}
 */
function clientCalls(source, names) {
  const pattern = new RegExp(`\\b(${names.join("|")})\\b`, "g");
  const stripped = stripCommentsAndReExports(source);
  return [...stripped.matchAll(pattern)].map((m) => ({
    line: lineOf(stripped, m.index ?? 0),
    name: m[1],
  }));
}

/** Prove the client-call placement sweep catches a named import, a namespace access, and a
 * renaming re-export, and ignores comments, plain re-export barrels, and longer identifiers (`bzh:case-pins-its-own-name`). */
function assertClientCallPlacementDetectorWorks() {
  const names = ["listChunksApiChunksGet", "getStatusStatusGet"];
  const hits = [
    "import { listChunksApiChunksGet } from 'fleet';",
    "import { foo,\n  listChunksApiChunksGet as list } from 'fleet';",
    "const r = await runnerApi.getStatusStatusGet({ throwOnError: true });",
    "const fns = { list: listChunksApiChunksGet }; // listChunksApiChunksGet",
    "export { listChunksApiChunksGet as listChunks } from './sdk.gen';",
  ];
  for (const source of hits) {
    if (clientCalls(source, names).length === 0) {
      throw new Error(
        `client-call placement sweep missed a client call in \`${source}\``,
      );
    }
  }
  const lined = clientCalls(
    "// listChunksApiChunksGet\n/* getStatusStatusGet */\nlistChunksApiChunksGet();",
    names,
  );
  if (lined.length !== 1 || lined[0].line !== 3) {
    throw new Error(
      "client-call placement sweep reported the wrong line or kept a comment",
    );
  }
  const misses = [
    "// listChunksApiChunksGet is called from chunks.query.ts",
    "/** Wraps `getStatusStatusGet`. */\nexport const x = 1;",
    "export { listChunksApiChunksGet, getStatusStatusGet } from './sdk.gen';",
    "export type {\n  listChunksApiChunksGet,\n} from './sdk.gen';",
    "/* Both SDKs share `getStatusStatusGet`. */\nexport * as hubApi from './api/hub';",
    "type T = ListChunksApiChunksGetData | listChunksApiChunksGetResponse;",
    "const url = `/api/${id}`; // getStatusStatusGet",
  ];
  for (const source of misses) {
    if (clientCalls(source, names).length > 0) {
      throw new Error(
        `client-call placement sweep false-positived on \`${source}\``,
      );
    }
  }
  if (!inClientCallScope("hub/src/app/board/board.ts")) {
    throw new Error(
      "client-call placement sweep scoped out a hand-written file",
    );
  }
  for (const rel of [
    "hub/src/app/board/board.spec.ts",
    "fleet/src/lib/kit/kit-button.stories.ts",
    "hub/src/app/board/chunks.query.ts",
    "hub/src/app/admin/assign-role.mutations.ts",
    "fleet/src/lib/api/runner/index.ts",
  ]) {
    if (inClientCallScope(rel))
      throw new Error(`client-call placement sweep scoped in \`${rel}\``);
  }
}

/** Generated client functions named outside query and mutation files, one report line each.
 * @param {string} [projectsDir] absolute
 * @returns {string[]}
 */
function clientCallPlacementViolations(projectsDir = PROJECTS_DIR) {
  const names = clientFunctionNames(projectsDir);
  /** @type {string[]} */
  const lines = [];
  for (const file of walkTs(projectsDir)) {
    const rel = toPosix(path.relative(projectsDir, file));
    if (!inClientCallScope(rel)) continue;
    for (const c of clientCalls(fs.readFileSync(file, "utf8"), names)) {
      lines.push(`  ${rel}:${c.line}: ${c.name}`);
    }
  }
  return lines;
}

/** The generated type modules a wire symbol is declared in, relative to `projects/`. */
const WIRE_TYPES_FILES = ["fleet/src/lib/api/hub/types.gen.ts", "fleet/src/lib/api/runner/types.gen.ts"];

/** The test-only transport home, relative to `projects/`. */
const TRANSPORT_DIR = "fleet/src/lib/testing/";
const TRANSPORT_SUBDIR = "fleet/src/lib/testing/transport/";

/** The boundary files' own suffixes; each one's focused spec is `<name>.spec.ts`. */
const BOUNDARY_SUFFIXES = [".query.ts", ".mutations.ts", ".translate.ts"];

/** Whether `rel` (`projects/`-relative, posix) may consume a wire symbol.
 * @param {string} rel
 */
function inDomainBoundary(rel) {
  if (rel.startsWith(GENERATED_API_DIR) || rel.startsWith(TRANSPORT_SUBDIR)) return true;
  const own = rel.endsWith(".spec.ts") ? rel.slice(0, -".spec.ts".length) + ".ts" : rel;
  return BOUNDARY_SUFFIXES.some((suffix) => own.endsWith(suffix));
}

/** Whether `rel` is held to the transport-reach rule: a production file or a story, not a spec and not a
 * test helper under `fleet/src/lib/testing/`.
 * @param {string} rel
 */
function inTransportReachScope(rel) {
  return !rel.endsWith(".spec.ts") && !rel.startsWith(TRANSPORT_DIR);
}

/** Every `.ts` file under `dir`, dot folders (`.storybook/`) included, declaration files excluded.
 * @param {string} dir absolute
 * @returns {string[]} absolute, posix
 */
function walkSources(dir) {
  return walkTs(dir)
    .map(toPosix)
    .filter((f) => !f.endsWith(".d.ts"));
}

/** Whether `node` sits inside a re-export statement (`export … from`, `export *`, `export * as`, or a
 * local `export { name }`) or an import declaration — a name passed through or bound, not referenced.
 * @param {ts.Node} node
 */
function inBindingStatement(node) {
  for (let at = node.parent; at; at = at.parent) {
    if (ts.isExportDeclaration(at) || ts.isImportDeclaration(at) || ts.isImportEqualsDeclaration(at)) return true;
    if (ts.isStatement(at) || ts.isSourceFile(at)) return false;
  }
  return false;
}

/** Whether `decl` declares a module's own export or the module itself — not a member of one, so reading
 * a field off a wire-typed value is not judged here, only naming the wire symbol.
 * @param {ts.Declaration} decl
 */
function isModuleLevel(decl) {
  if (ts.isSourceFile(decl) || ts.isSourceFile(decl.parent)) return true;
  return ts.isVariableDeclaration(decl) && ts.isSourceFile(decl.parent.parent.parent);
}

/** The declaring files of every module-level symbol `sf` references, each resolved through the full alias chain.
 * @param {ts.TypeChecker} checker
 * @param {ts.SourceFile} sf
 * @returns {Map<string, Set<string>>} declaring file (absolute, posix) -> the names referenced from it
 */
function referencedDeclarations(checker, sf) {
  /** @type {Map<string, Set<string>>} */
  const out = new Map();
  /** @param {ts.Node} node */
  const visit = (node) => {
    if (ts.isIdentifier(node) && !inBindingStatement(node)) {
      let symbol = checker.getSymbolAtLocation(node);
      if (symbol && symbol.flags & ts.SymbolFlags.Alias) symbol = checker.getAliasedSymbol(symbol);
      for (const decl of symbol?.declarations ?? []) {
        if (!isModuleLevel(decl)) continue;
        const file = toPosix(decl.getSourceFile().fileName);
        if (!out.has(file)) out.set(file, new Set());
        /** @type {Set<string>} */ (out.get(file)).add(node.text);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return out;
}

/**
 * @typedef {{ file: string, unit: string, names: string[] }} WireConsumer
 * @typedef {{ consumers: WireConsumer[], exempted: WireConsumer[], stale: string[],
 *   reach: { file: string, names: string[] }[] }} DomainBoundaryResult
 */

/**
 * Run the symbol-origin sweep over the tree at `root`.
 *
 * @param {{ root: string, memory?: Map<string, string> | null, unitOf: (rel: string) => string,
 *   exempt: readonly string[], strictScopes?: readonly string[] }} opts `unitOf` maps a `projects/`-relative file to its exemption unit;
 *   `strictScopes` are projects-relative directory prefixes, without a trailing slash
 * @returns {DomainBoundaryResult} unexempted consumers, consumers an exemption passed, exempt units
 *   that consume nothing, and transport reach — paths `projects/`-relative
 */
function sweepDomainBoundary({ root, memory = null, unitOf, exempt, strictScopes = [] }) {
  const projects = toPosix(path.join(root, "projects"));
  const files = memory ? [...memory.keys()] : walkSources(projects);
  const program = createProgram(root, files, memory);
  const checker = program.getTypeChecker();
  const wireFiles = new Set(WIRE_TYPES_FILES.map((rel) => `${projects}/${rel}`));
  const transport = `${projects}/${TRANSPORT_SUBDIR}`;

  /** @type {WireConsumer[]} */
  const consumers = [];
  /** @type {WireConsumer[]} */
  const exempted = [];
  /** @type {{ file: string, names: string[] }[]} */
  const reach = [];
  for (const file of files) {
    const rel = file.slice(projects.length + 1);
    const sf = program.getSourceFile(file);
    if (!sf) continue;
    const judgeWire = !inDomainBoundary(rel);
    const judgeReach = inTransportReachScope(rel);
    if (!judgeWire && !judgeReach) continue;
    const declarations = referencedDeclarations(checker, sf);
    /** @type {Set<string>} */
    const wireNames = new Set();
    /** @type {Set<string>} */
    const transportNames = new Set();
    for (const [declFile, names] of declarations) {
      if (wireFiles.has(declFile)) for (const n of names) wireNames.add(n);
      if (declFile.startsWith(transport)) for (const n of names) transportNames.add(n);
    }
    if (judgeWire && wireNames.size > 0) {
      const unit = unitOf(rel);
      const strict = strictScopes.some((scope) => rel.startsWith(`${scope}/`));
      (exempt.includes(unit) && !strict ? exempted : consumers).push({ file: rel, unit, names: [...wireNames].sort() });
    }
    if (judgeReach && transportNames.size > 0) reach.push({ file: rel, names: [...transportNames].sort() });
  }
  const stale = exempt.filter((unit) => !exempted.some((c) => c.unit === unit));
  return { consumers, exempted, stale, reach };
}

/**
 * Prove the symbol-origin detector can still fail, before trusting it over the tree
 * (`bzh:case-pins-its-own-name`). Every row of the boundary's detector-fixture table is planted as a
 * must-catch or a must-pass, `fleet` barrels and a same-name domain declaration included, beside an
 * exempted unit that fails without its entry and an entry that excuses nothing.
 */
function assertDomainBoundaryDetectorWorks() {
  const root = "/boundary-fixture";
  const p = `${root}/projects`;
  const wire = "../../../../fleet/src/lib/api/hub/types.gen";
  /** @type {[string, string][]} */
  const tree = [
    [
      "fleet/src/lib/api/hub/types.gen.ts",
      "export type ChunkDetail = { id: string };\nexport type RunnerView = { id: string };\n" +
        "export const ChunkStatus = { READY: 'ready' } as const;\n" +
        "export type ChunkStatus = typeof ChunkStatus[keyof typeof ChunkStatus];\n",
    ],
    ["fleet/src/lib/api/hub/index.ts", "export * from './types.gen';\n"],
    [
      "fleet/src/public-api.ts",
      "export type { ChunkDetail } from './lib/api/hub/types.gen';\nexport * from './lib/api/hub/index';\n" +
        "export * as hubApi from './lib/api/hub/index';\n",
    ],
    ["fleet/src/shell-api.ts", ""],
    // The test transport, its focused spec, a builder barrel leaking a generated type, and a helper over transport.
    [
      "fleet/src/lib/testing/transport/stub.ts",
      "import type { ChunkDetail } from '../../api/hub/types.gen';\nexport const stubRequestClient = (d: ChunkDetail) => d;\n",
    ],
    [
      "fleet/src/lib/testing/transport/stub.spec.ts",
      "import type { ChunkDetail } from '../../api/hub/types.gen';\nexport const d: ChunkDetail = { id: 'x' };\n",
    ],
    [
      "fleet/src/lib/testing/builders/chunk.translate.ts",
      "import type { ChunkDetail } from '../../api/hub/types.gen';\nexport type { ChunkDetail } from '../../api/hub/types.gen';\n" +
        "export const chunkDetail = (): ChunkDetail => ({ id: 'c' });\n",
    ],
    ["fleet/src/lib/testing/builders/index.ts", "export * from './chunk.translate';\n"],
    ["fleet/src/lib/testing/reach-helper.ts", "export { stubRequestClient as viaHelper } from './transport/stub';\n"],
    [
      "fleet/src/lib/testing/public-api.ts",
      "export * from './builders/index';\nexport { stubRequestClient } from './transport/stub';\nexport * from './reach-helper';\n",
    ],
    // An ordinary test helper outside the transport boundary.
    ["fleet/src/lib/testing/settle.ts", "import type { RunnerView } from '../api/hub/types.gen';\nexport let v: RunnerView;\n"],
    // A second barrel over `fleet`, and a domain declaration sharing a generated type's name.
    ["hub/src/app/core/wire-barrel.ts", "export { ChunkDetail as Detail } from 'fleet';\n"],
    ["hub/src/app/core/domain.ts", "export type ChunkDetail = { title: string };\n"],
    ["hub/src/app/core/index.ts", "export * from './domain';\n"],
    // Must-catch consumers outside the boundary.
    ["hub/src/app/alpha/direct.ts", `import type { ChunkDetail } from '${wire}';\nexport let d: ChunkDetail;\n`],
    ["hub/src/app/alpha/aliased.ts", `import type { ChunkDetail as Shape } from '${wire}';\nexport let d: Shape;\n`],
    ["hub/src/app/alpha/via-fleet-named.ts", "import type { ChunkDetail } from 'fleet';\nexport let d: ChunkDetail;\n"],
    ["hub/src/app/alpha/via-fleet-star.ts", "import type { RunnerView } from 'fleet';\nexport let r: RunnerView;\n"],
    ["hub/src/app/alpha/via-second-barrel.ts", "import type { Detail } from '../core/wire-barrel';\nexport let d: Detail;\n"],
    ["hub/src/app/alpha/via-namespace-type.ts", "import type { hubApi } from 'fleet';\nexport let s: hubApi.ChunkStatus;\n"],
    ["hub/src/app/alpha/via-namespace-value.ts", "import { hubApi } from 'fleet';\nexport const s = hubApi.ChunkStatus.READY;\n"],
    ["hub/src/app/alpha/via-namespace-import.ts", "import * as fleet from 'fleet';\nexport let s: fleet.RunnerView;\n"],
    ["hub/src/app/alpha/via-import-type.ts", "export let d: import('fleet').ChunkDetail;\n"],
    ["hub/src/app/alpha/alpha-view.spec.ts", "import type { ChunkDetail } from 'fleet';\nexport let d: ChunkDetail;\n"],
    ["hub/src/app/alpha/alpha.model.spec.ts", `import type { RunnerView } from '${wire}';\nexport let r: RunnerView;\n`],
    ["hub/src/app/alpha/alpha.stories.ts", "import type { ChunkDetail } from 'fleet/testing';\nexport let d: ChunkDetail;\n"],
    // Must-pass: a same-name domain type through a barrel, the boundary files and their focused specs.
    ["hub/src/app/beta/same-name.ts", "import type { ChunkDetail } from '../core';\nexport let d: ChunkDetail;\n"],
    ["hub/src/app/beta/beta.query.ts", "import type { ChunkDetail } from 'fleet';\nexport let d: ChunkDetail;\n"],
    ["hub/src/app/beta/beta.mutations.ts", "import { hubApi } from 'fleet';\nexport const s = hubApi.ChunkStatus.READY;\n"],
    ["hub/src/app/beta/beta.translate.ts", `import type { RunnerView } from '${wire}';\nexport let r: RunnerView;\n`],
    ["hub/src/app/beta/beta.query.spec.ts", "import type { ChunkDetail } from 'fleet';\nexport let d: ChunkDetail;\n"],
    ["hub/src/app/beta/beta.mutations.spec.ts", "import type { ChunkDetail } from 'fleet';\nexport let d: ChunkDetail;\n"],
    ["hub/src/app/beta/beta.translate.spec.ts", "import type { ChunkDetail } from 'fleet';\nexport let d: ChunkDetail;\n"],
    // Must-pass: a story using a builder, not a generated type; a spec reaching the transport.
    ["hub/src/app/beta/beta.stories.ts", "import { chunkDetail } from 'fleet/testing';\nexport const c = chunkDetail();\n"],
    ["hub/src/app/beta/beta-view.spec.ts", "import { stubRequestClient } from 'fleet/testing';\nexport const s = stubRequestClient;\n"],
    // Must-catch transport reach: production directly and through the entry, a story through a helper.
    ["hub/src/app/beta/reach-direct.ts", "import { stubRequestClient } from '../../../../fleet/src/lib/testing/transport/stub';\nexport const s = stubRequestClient;\n"],
    ["hub/src/app/beta/reach-entry.ts", "import { stubRequestClient } from 'fleet/testing';\nexport const s = stubRequestClient;\n"],
    ["hub/src/app/beta/reach-helper.stories.ts", "import { viaHelper } from 'fleet/testing';\nexport const s = viaHelper;\n"],
    // An exempted unit, and an exempt unit that consumes nothing.
    ["hub/src/app/gamma/g.ts", "import type { ChunkDetail } from 'fleet';\nexport let d: ChunkDetail;\n"],
    ["hub/src/app/delta/d.ts", "export const d = 1;\n"],
    ["hub/src/app/admin/projects/view.ts", "import type { Detail as Shape } from '../../core/wire-barrel';\nexport let d: Shape;\n"],
    ["hub/src/app/admin/projects/view.spec.ts", "import type { ChunkDetail } from 'fleet';\nexport let d: ChunkDetail;\n"],
    ["hub/src/app/admin/projects/project.query.ts", "import type { ChunkDetail } from 'fleet';\nexport let d: ChunkDetail;\n"],
    ["hub/src/app/admin/projects/project.query.spec.ts", "import type { ChunkDetail } from 'fleet';\nexport let d: ChunkDetail;\n"],
    ["hub/src/app/admin/projects/project.mutations.ts", "import type { ChunkDetail } from 'fleet';\nexport let d: ChunkDetail;\n"],
    ["hub/src/app/admin/projects/project.mutations.spec.ts", "import type { ChunkDetail } from 'fleet';\nexport let d: ChunkDetail;\n"],
    ["hub/src/app/admin/projects/project.translate.ts", "import type { ChunkDetail } from 'fleet';\nexport let d: ChunkDetail;\n"],
    ["hub/src/app/admin/projects/project.translate.spec.ts", "import type { ChunkDetail } from 'fleet';\nexport let d: ChunkDetail;\n"],
    ["hub/src/app/admin/sibling/view.ts", "import type { ChunkDetail } from 'fleet';\nexport let d: ChunkDetail;\n"],
  ];
  const memory = new Map(tree.map(([rel, text]) => [`${p}/${rel}`, text]));
  /** @param {string} rel */
  const unitOf = (rel) => rel.split("/").slice(0, 4).join("/");
  const run = (/** @type {string[]} */ exempt) => sweepDomainBoundary({ root, memory, unitOf, exempt });

  const { consumers, exempted, stale, reach } = run(["hub/src/app/gamma", "hub/src/app/delta", "hub/src/app/admin"]);
  const flagged = new Set(consumers.map((c) => c.file));
  const mustCatch = [
    "hub/src/app/alpha/direct.ts",
    "hub/src/app/alpha/aliased.ts",
    "hub/src/app/alpha/via-fleet-named.ts",
    "hub/src/app/alpha/via-fleet-star.ts",
    "hub/src/app/alpha/via-second-barrel.ts",
    "hub/src/app/alpha/via-namespace-type.ts",
    "hub/src/app/alpha/via-namespace-value.ts",
    "hub/src/app/alpha/via-namespace-import.ts",
    "hub/src/app/alpha/via-import-type.ts",
    "hub/src/app/alpha/alpha-view.spec.ts",
    "hub/src/app/alpha/alpha.model.spec.ts",
    "hub/src/app/alpha/alpha.stories.ts",
    "fleet/src/lib/testing/settle.ts",
  ];
  for (const file of mustCatch) {
    if (!flagged.has(file)) throw new Error(`domain-boundary detector missed the wire consumer ${file}`);
  }
  const mustPass = [...memory.keys()]
    .map((f) => f.slice(p.length + 1))
    .filter((rel) => !mustCatch.includes(rel));
  for (const file of mustPass) {
    if (flagged.has(file)) throw new Error(`domain-boundary detector false-positived on ${file}`);
  }
  if (!exempted.some((c) => c.file === "hub/src/app/gamma/g.ts")) {
    throw new Error("domain-boundary detector did not pass an exempted unit's consumer through its entry");
  }
  if (!run([]).consumers.some((c) => c.file === "hub/src/app/gamma/g.ts")) {
    throw new Error("domain-boundary detector passed an unexempted consumer");
  }
  if (stale.join() !== "hub/src/app/delta") {
    throw new Error(`domain-boundary detector misjudged stale exemptions (found: ${JSON.stringify(stale)})`);
  }
  const reached = new Set(reach.map((r) => r.file));
  for (const file of ["hub/src/app/beta/reach-direct.ts", "hub/src/app/beta/reach-entry.ts", "hub/src/app/beta/reach-helper.stories.ts"]) {
    if (!reached.has(file)) throw new Error(`domain-boundary detector missed transport reach from ${file}`);
  }
  for (const file of reached) {
    if (!file.includes("/reach-")) throw new Error(`domain-boundary detector false-positived transport reach from ${file}`);
  }

  const strictScopes = ["hub/src/app/admin/projects"];
  const admin = "hub/src/app/admin";
  const strictRun = (/** @type {Map<string, string>} */ sources) =>
    sweepDomainBoundary({ root, memory: sources, unitOf, exempt: [admin], strictScopes });
  const strictResult = strictRun(memory);
  for (const file of ["hub/src/app/admin/projects/view.ts", "hub/src/app/admin/projects/view.spec.ts"]) {
    if (!strictResult.consumers.some((c) => c.file === file)) throw new Error(`strict scope missed ${file}`);
  }
  for (const suffix of ["query.ts", "query.spec.ts", "mutations.ts", "mutations.spec.ts", "translate.ts", "translate.spec.ts"]) {
    const file = `hub/src/app/admin/projects/project.${suffix}`;
    if ([...strictResult.consumers, ...strictResult.exempted].some((c) => c.file === file)) {
      throw new Error(`strict scope rejected boundary file ${file}`);
    }
  }
  if (!strictResult.exempted.some((c) => c.file === "hub/src/app/admin/sibling/view.ts") || strictResult.stale.includes(admin)) {
    throw new Error("strict scope did not preserve the sibling exemption");
  }
  const withoutSibling = new Map(memory);
  withoutSibling.delete(`${p}/hub/src/app/admin/sibling/view.ts`);
  if (!strictRun(withoutSibling).stale.includes(admin)) {
    throw new Error("strict scope sustained a stale parent exemption");
  }
}

module.exports = {
  assertDomainBoundaryDetectorWorks,
  sweepDomainBoundary,
  inDomainBoundary,
  assertBackendCitationDetectorWorks,
  backendCitationViolations,
  assertClientCallPlacementDetectorWorks,
  clientCallPlacementViolations,
};
