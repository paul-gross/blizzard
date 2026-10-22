// @ts-check
/*
 * The open-vocabulary sweeps — `bzh:frontend-open-vocabulary`'s tooled half, run inside
 * `web:structural-gate` (`structural-gate.js`'s `main` calls them like any other sweep), with no
 * exemption list. A newer server may send a value the generated client does not list, so nothing
 * that branches on or looks up a wire value may assume the vocabulary is closed. ESLint's
 * `default-case` covers a TypeScript `switch`; these sweeps cover what lint cannot see.
 *
 * The template-switch sweep: every `@switch` block in a `.html` template under `projects/` carries
 * an `@default` branch of its own (a nested `@switch`'s `@default` does not count for its parent).
 *
 * The vocabulary-record sweep: a full table keyed by a generated vocabulary — a `Record` or a mapped
 * type over one (`{ [K in RunnerEventType]: … }`), a lookup that returns `undefined` for a value it
 * does not list — is built through `openLookup` instead. Lint
 * is not type-aware, so the sweep reads the key's written form: a generated vocabulary named
 * directly or through its namespace (`ChunkStatus`, `hubApi.EventLogSeverity`), a generated type's
 * vocabulary field (`HostRegistrationView['state']`), or a type alias declared in the same file as
 * either. The vocabularies and fields are read at run time from `fleet/src/lib/api/{hub,runner}/
 * types.gen.ts`. A `Partial<Record<…>>` or an optional mapped type is not full and passes; a table
 * written inside an `openLookup(…)` call, or declared by an unexported `const` this file reads only
 * inside `openLookup(…)` calls, is the helper's own table and passes. Scope is every `.ts` under
 * `projects/` except specs, stories, and the generated `fleet/src/lib/api/`.
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

/** The generated type modules the vocabularies are read from. */
const TYPES_FILES = ["fleet/src/lib/api/hub/types.gen.ts", "fleet/src/lib/api/runner/types.gen.ts"];

/** The helper a vocabulary-keyed table is built through. */
const HELPER = "openLookup";

/** @param {string} p */
const toPosix = (p) => p.split(path.sep).join("/");

/** Every file under `dir` with one of `extensions`.
 * @param {string} dir absolute
 * @param {string[]} extensions
 * @returns {string[]}
 */
function walk(dir, extensions) {
  /** @type {string[]} */
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules") continue;
      out.push(...walk(full, extensions));
    } else if (extensions.some((ext) => entry.name.endsWith(ext))) {
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

/** The index of the brace closing the one at `open`, or -1.
 * @param {string} source
 * @param {number} open
 */
function closingBrace(source, open) {
  let depth = 0;
  for (let i = open; i < source.length; i++) {
    if (source[i] === "{") depth++;
    else if (source[i] === "}" && --depth === 0) return i;
  }
  return -1;
}

/** The lines of every `@switch` block in `template` with no `@default` of its own.
 * @param {string} template
 * @returns {number[]}
 */
function bareTemplateSwitches(template) {
  /** @type {number[]} */
  const lines = [];
  for (const match of template.matchAll(/@switch\s*\(/g)) {
    const start = match.index ?? 0;
    const open = template.indexOf("{", start);
    const close = open === -1 ? -1 : closingBrace(template, open);
    if (close === -1) continue;
    let body = template.slice(open + 1, close);
    // Blank every nested @switch block, so only this block's own branches remain.
    for (const nested of [...body.matchAll(/@switch\s*\(/g)].reverse()) {
      const nestedOpen = body.indexOf("{", nested.index ?? 0);
      const nestedClose = nestedOpen === -1 ? -1 : closingBrace(body, nestedOpen);
      if (nestedClose !== -1) body = body.slice(0, nested.index) + body.slice(nestedClose + 1);
    }
    if (!/@default\b/.test(body)) lines.push(lineOf(template, start));
  }
  return lines;
}

/**
 * @typedef {{ vocabularies: Set<string>, fields: Map<string, Set<string>> }} Vocabulary
 * `vocabularies`: every generated vocabulary's name. `fields`: per generated object type, the
 * fields whose type is a vocabulary or an inline string-literal union.
 */

/** Whether a field's declared type is a vocabulary: a vocabulary name, or a union of string literals.
 * @param {ts.TypeNode} type
 * @param {Set<string>} vocabularies
 */
function isVocabularyType(type, vocabularies) {
  if (ts.isTypeReferenceNode(type) && ts.isIdentifier(type.typeName)) return vocabularies.has(type.typeName.text);
  if (!ts.isUnionTypeNode(type)) return false;
  const members = type.types.filter(
    (t) => !(ts.isLiteralTypeNode(t) && t.literal.kind === ts.SyntaxKind.NullKeyword) && t.kind !== ts.SyntaxKind.UndefinedKeyword,
  );
  if (members.length === 1) return isVocabularyType(members[0], vocabularies);
  return members.length > 1 && members.every((t) => ts.isLiteralTypeNode(t) && ts.isStringLiteral(t.literal));
}

/** The vocabularies and vocabulary fields `sources` (generated type modules) declare.
 * @param {string[]} sources
 * @returns {Vocabulary}
 */
function readVocabulary(sources) {
  const vocabularies = new Set();
  for (const source of sources) {
    for (const m of source.matchAll(/^export type (\w+) = typeof \1\[keyof typeof \1\];/gm)) vocabularies.add(m[1]);
  }
  /** @type {Map<string, Set<string>>} */
  const fields = new Map();
  for (const source of sources) {
    const sf = ts.createSourceFile("types.gen.ts", source, ts.ScriptTarget.Latest, false, ts.ScriptKind.TS);
    for (const statement of sf.statements) {
      if (!ts.isTypeAliasDeclaration(statement) || !ts.isTypeLiteralNode(statement.type)) continue;
      const named = new Set();
      for (const member of statement.type.members) {
        if (!ts.isPropertySignature(member) || !member.type || !member.name) continue;
        const name = ts.isIdentifier(member.name) || ts.isStringLiteral(member.name) ? member.name.text : null;
        if (name !== null && isVocabularyType(member.type, vocabularies)) named.add(name);
      }
      if (named.size > 0) fields.set(statement.name.text, new Set([...(fields.get(statement.name.text) ?? []), ...named]));
    }
  }
  return { vocabularies, fields };
}

/** The unqualified name a type reference writes — `ChunkStatus` for `hubApi.ChunkStatus`.
 * @param {ts.EntityName} name
 */
function rightmost(name) {
  return ts.isIdentifier(name) ? name.text : name.right.text;
}

/** Whether `type` names a vocabulary in one of the written forms the sweep reads.
 * @param {ts.TypeNode} type
 * @param {Vocabulary} vocabulary
 * @param {Map<string, ts.TypeNode>} aliases this file's own type aliases
 * @param {Set<string>} [seen]
 */
function namesVocabulary(type, vocabulary, aliases, seen = new Set()) {
  if (ts.isParenthesizedTypeNode(type)) return namesVocabulary(type.type, vocabulary, aliases, seen);
  if (ts.isTypeReferenceNode(type) && !type.typeArguments) {
    const name = rightmost(type.typeName);
    if (ts.isIdentifier(type.typeName) && aliases.has(name) && !seen.has(name)) {
      seen.add(name);
      return namesVocabulary(/** @type {ts.TypeNode} */ (aliases.get(name)), vocabulary, aliases, seen);
    }
    return vocabulary.vocabularies.has(name);
  }
  if (
    ts.isIndexedAccessTypeNode(type) &&
    ts.isTypeReferenceNode(type.objectType) &&
    ts.isLiteralTypeNode(type.indexType) &&
    ts.isStringLiteral(type.indexType.literal)
  ) {
    return vocabulary.fields.get(rightmost(type.objectType.typeName))?.has(type.indexType.literal.text) ?? false;
  }
  return false;
}

/** Whether `node` sits inside a call to the helper.
 * @param {ts.Node} node
 */
function insideHelperCall(node) {
  for (let at = node.parent; at; at = at.parent) {
    if (ts.isCallExpression(at) && ts.isIdentifier(at.expression) && at.expression.text === HELPER) return true;
  }
  return false;
}

/** Whether the table type `node` annotates an unexported `const` this file reads, and reads only
 * inside helper calls.
 * @param {ts.Node} node
 * @param {ts.SourceFile} sf
 */
function onlyReadThroughHelper(node, sf) {
  let at = node.parent;
  while (at && !ts.isVariableDeclaration(at) && !ts.isStatement(at)) at = at.parent;
  if (!at || !ts.isVariableDeclaration(at) || !ts.isIdentifier(at.name) || !at.type) return false;
  if (node.getStart(sf) < at.type.getStart(sf) || node.getEnd() > at.type.getEnd()) return false;
  const statement = at.parent.parent;
  if (!(at.parent.flags & ts.NodeFlags.Const) || !ts.isVariableStatement(statement)) return false;
  if (statement.modifiers?.some((m) => m.kind === ts.SyntaxKind.ExportKeyword)) return false;
  const name = at.name.text;
  const declared = at.name;
  let reads = 0;
  let outside = false;
  /** @param {ts.Node} n */
  const visit = (n) => {
    if (ts.isIdentifier(n) && n !== declared && n.text === name) {
      reads += 1;
      if (!insideHelperCall(n)) outside = true;
    }
    ts.forEachChild(n, visit);
  };
  visit(sf);
  return reads > 0 && !outside;
}

/** The lines of every full table in `source` keyed by a vocabulary and not built through the helper.
 * @param {string} source
 * @param {Vocabulary} vocabulary
 * @returns {{ line: number, text: string }[]}
 */
function vocabularyRecords(source, vocabulary) {
  const sf = ts.createSourceFile("sweep.ts", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  /** @type {Map<string, ts.TypeNode>} */
  const aliases = new Map();
  for (const statement of sf.statements) {
    if (ts.isTypeAliasDeclaration(statement)) aliases.set(statement.name.text, statement.type);
  }
  /** @type {{ line: number, text: string }[]} */
  const hits = [];
  /** @param {ts.Node} node */
  const visit = (node) => {
    const fullRecord =
      ts.isTypeReferenceNode(node) &&
      ts.isIdentifier(node.typeName) &&
      node.typeName.text === "Record" &&
      node.typeArguments?.length === 2 &&
      namesVocabulary(node.typeArguments[0], vocabulary, aliases) &&
      !(ts.isTypeReferenceNode(node.parent) && ts.isIdentifier(node.parent.typeName) && node.parent.typeName.text === "Partial");
    const fullMapped =
      ts.isMappedTypeNode(node) &&
      node.questionToken?.kind !== ts.SyntaxKind.QuestionToken &&
      node.typeParameter.constraint !== undefined &&
      namesVocabulary(node.typeParameter.constraint, vocabulary, aliases);
    if ((fullRecord || fullMapped) && !insideHelperCall(node) && !onlyReadThroughHelper(node, sf)) {
      hits.push({ line: lineOf(source, node.getStart(sf)), text: node.getText(sf) });
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return hits;
}

/** @param {string} rel `projects/`-relative posix path */
function inRecordScope(rel) {
  return rel.endsWith(".ts") && !rel.endsWith(".spec.ts") && !rel.endsWith(".stories.ts") && !rel.startsWith(GENERATED_API_DIR);
}

/** Prove the template-switch sweep catches a bare `@switch`, nested or not, and passes one with
 * its own `@default` (`bzh:case-pins-its-own-name`). */
function assertTemplateSwitchDetectorWorks() {
  const bare = "<p>\n@switch (s) {\n  @case ('a') { A }\n}\n</p>";
  if (bareTemplateSwitches(bare).join() !== "2") throw new Error("template-switch sweep missed a bare @switch");
  const nestedBare = "@switch (a) {\n  @case (1) {\n    @switch (b) { @case (2) { x } }\n  }\n  @default { y }\n}";
  if (bareTemplateSwitches(nestedBare).join() !== "3") {
    throw new Error("template-switch sweep missed a bare nested @switch, or failed its defaulted parent");
  }
  const parentBare = "@switch (a) {\n  @case (1) {\n    @switch (b) { @case (2) { x } @default { z } }\n  }\n}";
  if (bareTemplateSwitches(parentBare).join() !== "1") {
    throw new Error("template-switch sweep credited a nested @default to its parent");
  }
  const defaulted = "@switch (s) {\n  @case ('a') { A }\n  @default { {{ s }} }\n}";
  if (bareTemplateSwitches(defaulted).length > 0) throw new Error("template-switch sweep false-positived on an @default");
}

/** Prove the vocabulary-record sweep catches a `Record` keyed in every written form and a mapped type
 * over a vocabulary, and passes the helper's form, a table read only through the helper, a `Partial`
 * or optional mapped type, and a non-vocabulary key (`bzh:case-pins-its-own-name`). */
function assertVocabularyRecordDetectorWorks() {
  const vocabulary = readVocabulary([
    [
      "export const ChunkStatus = { READY: 'ready' } as const;",
      "export type ChunkStatus = typeof ChunkStatus[keyof typeof ChunkStatus];",
      "export const EventLogSeverity = { INFO: 'info' } as const;",
      "export type EventLogSeverity = typeof EventLogSeverity[keyof typeof EventLogSeverity];",
      "export type HostRegistrationView = {",
      "    state: 'pending' | 'registered';",
      "    severity?: EventLogSeverity | null;",
      "    host_id?: string | null;",
      "};",
    ].join("\n"),
  ]);
  const hits = [
    "const A: Record<ChunkStatus, string> = {};",
    "const A: Readonly<Record<hubApi.EventLogSeverity, string>> = {};",
    "const A: Record<HostRegistrationView['state'], string> = {};",
    "const A: Record<runnerApi.HostRegistrationView['state'], string> = {};",
    "const A: Record<HostRegistrationView['severity'], string> = {};",
    "type S = hubApi.ChunkStatus;\nconst A: Record<S, string> = {};",
    "type F = HostRegistrationView['state'];\ntype G = F;\nconst A: Record<G, string> = {};",
    "const r = registry as Record<ChunkStatus, () => void>;",
    "const A: { [K in ChunkStatus]: string } = {};",
    "const A: { readonly [K in hubApi.ChunkStatus]: (k: K) => void } = {};\nA.ready;",
    "const A: { [K in ChunkStatus]: string } = {};\nconst B = openLookup(A, () => '');\nA.ready;",
    "export const A: { [K in ChunkStatus]: string } = {};\nconst B = openLookup(A, () => '');",
  ];
  for (const source of hits) {
    if (vocabularyRecords(source, vocabulary).length !== 1) {
      throw new Error(`vocabulary-record sweep missed the table in \`${source}\``);
    }
  }
  if (vocabularyRecords("const a = 1;\nconst A: Record<ChunkStatus, string> = {};", vocabulary)[0]?.line !== 2) {
    throw new Error("vocabulary-record sweep reported the wrong line");
  }
  const misses = [
    "const A = openLookup<ChunkStatus, string>({ ready: 'r' }, (raw) => raw);",
    "const A = openLookup(registry as Record<ChunkStatus, () => void>, () => () => undefined);",
    "const A: Partial<Record<ChunkStatus, string>> = {};",
    "const A: Record<string, string> = {};",
    "const A: Record<Tone, string> = {};",
    "const A: Record<HostRegistrationView['host_id'], string> = {};",
    "type S = 'a' | 'b';\nconst A: Record<S, string> = {};",
    "const A: { [K in ChunkStatus]?: string } = {};",
    "const A: { [K in Tone]: string } = {};",
    "const A: { [K in ChunkStatus]: string } = {};\nconst B = openLookup(A as Record<ChunkStatus, string>, () => '');",
    "const A: Record<ChunkStatus, string> = {};\nconst B = openLookup(A, () => '');",
  ];
  for (const source of misses) {
    if (vocabularyRecords(source, vocabulary).length > 0) {
      throw new Error(`vocabulary-record sweep false-positived on \`${source}\``);
    }
  }
  if (!inRecordScope("hub/src/app/events/events-view.ts")) throw new Error("vocabulary-record sweep scoped out a hand-written file");
  for (const rel of ["hub/src/app/events/events-view.spec.ts", "fleet/src/lib/kit/kit-badge.stories.ts", "fleet/src/lib/api/hub/types.gen.ts"]) {
    if (inRecordScope(rel)) throw new Error(`vocabulary-record sweep scoped in \`${rel}\``);
  }
}

/** `@switch` blocks with no `@default` under `projects/`, one report line each.
 * @param {string} [projectsDir] absolute
 * @returns {string[]}
 */
function templateSwitchViolations(projectsDir = PROJECTS_DIR) {
  /** @type {string[]} */
  const lines = [];
  for (const file of walk(projectsDir, [".html"])) {
    const rel = toPosix(path.relative(projectsDir, file));
    for (const line of bareTemplateSwitches(fs.readFileSync(file, "utf8"))) lines.push(`  ${rel}:${line}: @switch with no @default`);
  }
  return lines;
}

/** Vocabulary-keyed full tables under `projects/` not built through the helper, one report line each.
 * @param {string} [projectsDir] absolute
 * @returns {string[]}
 */
function vocabularyRecordViolations(projectsDir = PROJECTS_DIR) {
  const vocabulary = readVocabulary(TYPES_FILES.map((rel) => fs.readFileSync(path.join(projectsDir, rel), "utf8")));
  if (vocabulary.vocabularies.size === 0) {
    throw new Error(`vocabulary-record sweep found no vocabularies in ${TYPES_FILES.join(", ")}`);
  }
  /** @type {string[]} */
  const lines = [];
  for (const file of walk(projectsDir, [".ts"])) {
    const rel = toPosix(path.relative(projectsDir, file));
    if (!inRecordScope(rel)) continue;
    for (const hit of vocabularyRecords(fs.readFileSync(file, "utf8"), vocabulary)) lines.push(`  ${rel}:${hit.line}: ${hit.text}`);
  }
  return lines;
}

module.exports = {
  assertTemplateSwitchDetectorWorks,
  templateSwitchViolations,
  assertVocabularyRecordDetectorWorks,
  vocabularyRecordViolations,
};
