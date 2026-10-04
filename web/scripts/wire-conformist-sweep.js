// @ts-check
/*
 * The wire-conformist sweeps — the frontend conforms to the hub and runner wire through the
 * generated clients under `fleet/src/lib/api/`, run inside `web:structural-gate`
 * (`structural-gate.js`'s `main` calls them like any other sweep). Neither sweep has an
 * exemption list.
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
 * Run through `npm run structural-gate`; this module only exports.
 */

const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

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

module.exports = {
  assertBackendCitationDetectorWorks,
  backendCitationViolations,
  assertClientCallPlacementDetectorWorks,
  clientCallPlacementViolations,
};
