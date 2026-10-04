// @ts-check
/*
 * The containers-compose sweep — the tooled half of `bzh:frontend-containers-compose`, run inside
 * `web:structural-gate` (`structural-gate.js`'s `main` calls it like any other sweep). It has no
 * exemption list.
 *
 * A container's `computed()` only composes: it reads signals and query results and calls imported
 * functions. Branching, loops, and collection transforms live in a pure `*.model.ts` beside the
 * feature. The sweep resolves each piece with the TypeScript compiler, over every non-spec `.ts`
 * under `projects/` outside the generated `fleet/src/lib/api/`:
 *
 * - **Query-bearing helpers** are a fixed point, read from the source on every run: a top-level
 *   `function inject*` or `const inject* = <arrow | function>` is query-bearing when its body calls,
 *   by bare identifier, one of `TANSTACK_INJECTORS` or another query-bearing helper. A new wrapper is
 *   recognized the day it is written, whatever its name.
 * - **A container** is a class carrying a `@Component(...)` decorator whose members call a
 *   query-bearing helper by bare identifier.
 * - **A `computed()` call** is a call, inside a container class, of the identifier `@angular/core`'s
 *   `computed` is imported as (alias-aware); a local function named `computed` is not one.
 * - **Deriving** is any of `DERIVING_SYNTAX` or a `.<name>(…)` call named in `TRANSFORMS` (optional
 *   chain included) anywhere in the callback's subtree, nested arrows included, and in the body of
 *   every same-class method, getter, or function-valued property the callback reaches through
 *   `this.<member>`, followed transitively. `&&`, `||`, `??`, `?.`, comparisons, template literals,
 *   and calls of imported functions are composition and pass.
 *
 * Run through `npm run structural-gate`; this module only exports.
 */

const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const ROOT = path.resolve(__dirname, '..');
const PROJECTS_DIR = path.join(ROOT, 'projects');

/** The generated clients, relative to `projects/`; nothing under it is hand-written. */
const GENERATED_API_DIR = 'fleet/src/lib/api/';

/** The TanStack Query injectors every query-bearing helper bottoms out in. */
const TANSTACK_INJECTORS = [
  'injectQuery',
  'injectMutation',
  'injectMutationState',
  'injectInfiniteQuery',
  'injectQueries',
  'injectQueryClient',
];

/** Statement and expression kinds that branch or loop, with the label a report line prints. */
const DERIVING_SYNTAX = new Map([
  [ts.SyntaxKind.IfStatement, 'if'],
  [ts.SyntaxKind.SwitchStatement, 'switch'],
  [ts.SyntaxKind.ConditionalExpression, 'ternary'],
  [ts.SyntaxKind.ForStatement, 'for'],
  [ts.SyntaxKind.ForOfStatement, 'for-of'],
  [ts.SyntaxKind.ForInStatement, 'for-in'],
  [ts.SyntaxKind.WhileStatement, 'while'],
  [ts.SyntaxKind.DoStatement, 'do-while'],
]);

/** Collection-transform method names: a `.<name>(…)` call of one derives. */
const TRANSFORMS = new Set([
  'filter',
  'map',
  'flatMap',
  'reduce',
  'reduceRight',
  'sort',
  'toSorted',
  'find',
  'findIndex',
  'findLast',
  'findLastIndex',
  'some',
  'every',
  'forEach',
]);

const HEADER = 'deriving computed() callbacks in containers (bzh:frontend-containers-compose)';

/** @param {string} p */
const toPosix = (p) => p.split(path.sep).join('/');

/** @param {string} rel `projects/`-relative posix path */
function inScope(rel) {
  return (
    rel.endsWith('.ts') && !rel.endsWith('.spec.ts') && !rel.endsWith('.d.ts') && !rel.startsWith(GENERATED_API_DIR)
  );
}

/** Every in-scope `.ts` under `projectsDir`, keyed by `projects/`-relative posix path.
 * @param {string} projectsDir absolute
 * @returns {Map<string, string>}
 */
function readSources(projectsDir) {
  /** @type {Map<string, string>} */
  const sources = new Map();
  /** @param {string} dir */
  const visit = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) visit(full);
      else if (entry.isFile()) {
        const rel = toPosix(path.relative(projectsDir, full));
        if (inScope(rel)) sources.set(rel, fs.readFileSync(full, 'utf8'));
      }
    }
  };
  visit(projectsDir);
  return sources;
}

/** @param {string} rel @param {string} source */
const parse = (rel, source) => ts.createSourceFile(rel, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);

/** Every identifier `node`'s subtree calls bare (`name(…)`, not `x.name(…)`).
 * @param {ts.Node} node
 * @returns {Set<string>}
 */
function bareCalls(node) {
  /** @type {Set<string>} */
  const names = new Set();
  /** @param {ts.Node} n */
  const visit = (n) => {
    if (ts.isCallExpression(n) && ts.isIdentifier(n.expression)) names.add(n.expression.text);
    n.forEachChild(visit);
  };
  visit(node);
  return names;
}

/** The query-bearing `inject*` helpers declared at the top level of `files`: the fixed point grown
 * from `TANSTACK_INJECTORS`.
 * @param {Map<string, ts.SourceFile>} files
 * @returns {Set<string>}
 */
function queryBearingHelpers(files) {
  /** @type {Map<string, Set<string>>} */
  const callsByHelper = new Map();
  /** @param {string} name @param {ts.Node} body */
  const record = (name, body) => {
    const calls = callsByHelper.get(name) ?? new Set();
    for (const c of bareCalls(body)) calls.add(c);
    callsByHelper.set(name, calls);
  };
  for (const sf of files.values()) {
    for (const stmt of sf.statements) {
      if (ts.isFunctionDeclaration(stmt) && stmt.name && stmt.body && stmt.name.text.startsWith('inject')) {
        record(stmt.name.text, stmt.body);
      } else if (ts.isVariableStatement(stmt)) {
        for (const decl of stmt.declarationList.declarations) {
          const init = decl.initializer;
          if (
            ts.isIdentifier(decl.name) &&
            decl.name.text.startsWith('inject') &&
            init &&
            (ts.isArrowFunction(init) || ts.isFunctionExpression(init))
          ) {
            record(decl.name.text, init.body);
          }
        }
      }
    }
  }
  const helpers = new Set(TANSTACK_INJECTORS);
  let grew = true;
  while (grew) {
    grew = false;
    for (const [name, calls] of callsByHelper) {
      if (!helpers.has(name) && [...calls].some((c) => helpers.has(c))) {
        helpers.add(name);
        grew = true;
      }
    }
  }
  return helpers;
}

/** The local names `@angular/core`'s `computed` is imported under in `sf`.
 * @param {ts.SourceFile} sf
 * @returns {Set<string>}
 */
function computedBindings(sf) {
  /** @type {Set<string>} */
  const names = new Set();
  for (const stmt of sf.statements) {
    if (
      !ts.isImportDeclaration(stmt) ||
      !ts.isStringLiteral(stmt.moduleSpecifier) ||
      stmt.moduleSpecifier.text !== '@angular/core'
    )
      continue;
    const bindings = stmt.importClause?.namedBindings;
    if (!bindings || !ts.isNamedImports(bindings)) continue;
    for (const spec of bindings.elements) {
      if ((spec.propertyName ?? spec.name).text === 'computed') names.add(spec.name.text);
    }
  }
  return names;
}

/** @param {ts.ClassDeclaration} cls */
function isComponent(cls) {
  return (ts.getDecorators(cls) ?? []).some(
    (d) =>
      ts.isCallExpression(d.expression) &&
      ts.isIdentifier(d.expression.expression) &&
      d.expression.expression.text === 'Component',
  );
}

/** The bodies of `cls`'s methods, getters, and function-valued properties, by member name.
 * @param {ts.ClassDeclaration} cls
 * @returns {Map<string, ts.Node>}
 */
function memberBodies(cls) {
  /** @type {Map<string, ts.Node>} */
  const bodies = new Map();
  for (const m of cls.members) {
    if (!m.name || !(ts.isIdentifier(m.name) || ts.isPrivateIdentifier(m.name))) continue;
    if ((ts.isMethodDeclaration(m) || ts.isGetAccessorDeclaration(m)) && m.body) bodies.set(m.name.text, m.body);
    else if (
      ts.isPropertyDeclaration(m) &&
      m.initializer &&
      (ts.isArrowFunction(m.initializer) || ts.isFunctionExpression(m.initializer))
    ) {
      bodies.set(m.name.text, m.initializer.body);
    }
  }
  return bodies;
}

/** The deriving kinds in `fn`'s subtree, in source order and deduplicated, following every
 * `this.<member>` that names a same-class body in `bodies` once (`seen`), transitively.
 * @param {ts.Node} fn
 * @param {Map<string, ts.Node>} bodies
 * @param {Set<string>} seen
 * @param {string} via `''`, or ` via this.<member>` for a followed body
 * @param {string[]} kinds accumulator
 */
function derivingKinds(fn, bodies, seen, via, kinds) {
  /** @param {string} kind */
  const add = (kind) => {
    if (!kinds.includes(kind + via)) kinds.push(kind + via);
  };
  /** @param {ts.Node} n */
  const visit = (n) => {
    const syntax = DERIVING_SYNTAX.get(n.kind);
    if (syntax) add(syntax);
    if (ts.isCallExpression(n) && ts.isPropertyAccessExpression(n.expression)) {
      const method = n.expression.name.text;
      if (TRANSFORMS.has(method)) add(`.${method}()`);
    }
    if (ts.isPropertyAccessExpression(n) && n.expression.kind === ts.SyntaxKind.ThisKeyword) {
      const member = n.name.text;
      const body = bodies.get(member);
      if (body && !seen.has(member)) {
        seen.add(member);
        derivingKinds(body, bodies, seen, ` via this.${member}`, kinds);
      }
    }
    n.forEachChild(visit);
  };
  visit(fn);
  return kinds;
}

/** The name a `computed()` call is bound to: the nearest enclosing field, variable, property, or
 * assignment target.
 * @param {ts.Node} call
 * @param {ts.SourceFile} sf
 */
function boundName(call, sf) {
  for (let p = call.parent; p; p = p.parent) {
    if (ts.isPropertyDeclaration(p) || ts.isVariableDeclaration(p) || ts.isPropertyAssignment(p))
      return p.name.getText(sf);
    if (ts.isBinaryExpression(p) && p.operatorToken.kind === ts.SyntaxKind.EqualsToken) return p.left.getText(sf);
    if (ts.isClassDeclaration(p)) break;
  }
  return '<anonymous>';
}

/** Deriving `computed()` callbacks in container classes across `sources`, one report line each.
 * @param {Map<string, string>} sources `projects/`-relative posix path → text, all in scope
 * @returns {string[]}
 */
function derivingComputedLines(sources) {
  /** @type {Map<string, ts.SourceFile>} */
  const files = new Map();
  for (const [rel, source] of sources) files.set(rel, parse(rel, source));
  const helpers = queryBearingHelpers(files);
  /** @type {string[]} */
  const lines = [];
  for (const [rel, sf] of [...files].sort(([a], [b]) => a.localeCompare(b))) {
    const computed = computedBindings(sf);
    if (computed.size === 0) continue;
    /** @param {ts.Node} node */
    const visitClasses = (node) => {
      if (ts.isClassDeclaration(node) && isComponent(node) && [...bareCalls(node)].some((c) => helpers.has(c))) {
        const bodies = memberBodies(node);
        /** @param {ts.Node} n */
        const visitCalls = (n) => {
          if (
            ts.isCallExpression(n) &&
            ts.isIdentifier(n.expression) &&
            computed.has(n.expression.text) &&
            n.arguments[0]
          ) {
            const kinds = derivingKinds(n.arguments[0], bodies, new Set(), '', []);
            if (kinds.length > 0) {
              const line = sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1;
              lines.push(`  projects/${rel}:${line} computed ${boundName(n, sf)}: ${kinds.join(', ')}`);
            }
          }
          n.forEachChild(visitCalls);
        };
        node.forEachChild(visitCalls);
      }
      node.forEachChild(visitClasses);
    };
    visitClasses(sf);
  }
  return lines;
}

/** Deriving `computed()` callbacks in containers under `projects/`, one report line each.
 * @param {string} [projectsDir] absolute
 * @returns {string[]}
 */
function containersComposeViolations(projectsDir = PROJECTS_DIR) {
  return derivingComputedLines(readSources(projectsDir));
}

/** Seeded sources for the self-test, `projects/`-relative path → text. */
const SEEDED_SOURCES = {
  'app/src/rows.query.ts': `
import { injectQuery } from '@tanstack/angular-query-experimental';
export function injectRowsQuery() { return injectQuery(() => ({ queryKey: ['rows'] })); }
`,
  'app/src/row-lookup.ts': `
import { computed } from '@angular/core';
import { injectRowsQuery } from './rows.query';
export const injectRowLookup = function () {
  const rows = injectRowsQuery();
  return computed(() => (rows.data() ?? []).map((r) => r.id));
};
export const injectClock = () => Date.now();
`,
  'app/src/kinds.ts': `
import { Component, computed } from '@angular/core';
import { injectRowsQuery } from './rows.query';
@Component({ selector: 'app-kinds', template: '' })
export class Kinds {
  readonly query = injectRowsQuery();
  readonly a = computed(() => { if (this.query.isPending()) return 1; return 2; });
  readonly b = computed(() => { switch (this.query.status()) { default: return 1; } });
  readonly c = computed(() => (this.query.isPending() ? 1 : 2));
  readonly d = computed(() => { let n = 0; for (let i = 0; i < 3; i++) n += i; return n; });
  readonly e = computed(() => { let n = 0; for (const x of [1]) n += x; return n; });
  readonly f = computed(() => { let n = ''; for (const k in {}) n += k; return n; });
  readonly g = computed(() => { let n = 0; while (n < 3) n++; return n; });
  readonly h = computed(() => { let n = 0; do n++; while (n < 3); return n; });
  readonly i = computed(() => this.query.data()?.filter((r) => r.live));
  readonly j = computed(() => this.query.data()!.items.toSorted());
  readonly k = computed(() => [1].map((x) => [x].find((y) => (y ? y : 0))));
  readonly l = computed<number>(() => (this.query.isPending() ? 1 : 2));
  readonly m = computed(() => { const xs = this.query.data()!.items; return [xs.filter(f), xs.map(f), xs.flatMap(f), xs.reduce(f), xs.reduceRight(f), xs.sort(f), xs.toSorted(f), xs.find(f), xs.findIndex(f), xs.findLast(f), xs.findLastIndex(f), xs.some(f), xs.every(f), xs.forEach(f)]; });
}
`,
  'app/src/composing.ts': `
import { Component, computed as derive } from '@angular/core';
import { asyncState } from 'fleet';
import { injectRowsQuery } from './rows.query';
@Component({ selector: 'app-composing', template: '' })
export class Composing {
  readonly query = injectRowsQuery();
  readonly pending = signal(false);
  readonly state = derive(() => asyncState(this.query, (this.query.data() ?? []).length === 0));
  readonly fallback = derive(() => this.query.data() ?? this.pending());
  readonly logic = derive(() => (this.pending() && this.query.isError()) || this.query.data()?.done === true);
  readonly label = derive(() => \`\${this.query.data()?.title} (\${this.pending()})\`);
  readonly viaHelper = derive(() => this.helper());
  readonly viaChain = derive(() => this.first());
  readonly viaGetter = derive(() => this.choice);
  readonly viaArrow = derive(() => this.arrow(1));
  readonly recursive = derive(() => this.loop(3));
  readonly signalRead = derive(() => this.pending());
  onClick(): number { return this.pending() ? 1 : 2; }
  private helper(): number { return this.pending() ? 1 : 2; }
  private first(): number { return this.second(); }
  private second(): number { if (this.pending()) return 1; return 2; }
  private get choice(): number { return this.query.data()?.items.some(Boolean) ? 1 : 0; }
  private readonly arrow = (n: number) => [n].filter(Boolean);
  private loop(n: number): number { return this.loop(n - 1) + this.loop(n - 2); }
}
`,
  'app/src/wrapped.ts': `
import { Component, computed } from '@angular/core';
import { injectRowLookup } from './row-lookup';
@Component({ selector: 'app-wrapped', template: '' })
export class Wrapped {
  readonly ids = injectRowLookup();
  readonly first = computed(() => this.ids().find(Boolean));
}
`,
  'app/src/presentational.ts': `
import { Component, computed, input } from '@angular/core';
import { injectClock } from './row-lookup';
@Component({ selector: 'app-presentational', template: '' })
export class Presentational {
  readonly now = injectClock();
  readonly busy = input(false);
  readonly label = computed(() => (this.busy() ? 'busy' : 'idle'));
}
`,
  'app/src/service.ts': `
import { Injectable, computed } from '@angular/core';
import { injectRowsQuery } from './rows.query';
@Injectable({ providedIn: 'root' })
export class RowsService {
  readonly query = injectRowsQuery();
  readonly ids = computed(() => (this.query.data() ?? []).map((r) => r.id));
}
`,
  'app/src/local-computed.ts': `
import { Component, computed as ngComputed, signal } from '@angular/core';
import { injectRowsQuery } from './rows.query';
const computed = <T>(fn: () => T) => fn;
@Component({ selector: 'app-local', template: '' })
export class LocalComputed {
  readonly query = injectRowsQuery();
  readonly label = computed(() => (this.query.isPending() ? 'a' : 'b'));
  readonly data = ngComputed(() => this.query.data());
  readonly mode = signal(this.query.isPending() ? 'a' : 'b');
}
`,
  'app/src/foreign-computed.ts': `
import { Component } from '@angular/core';
import { computed } from 'signal-polyfill';
import { injectRowsQuery } from './rows.query';
@Component({ selector: 'app-foreign', template: '' })
export class ForeignComputed {
  readonly query = injectRowsQuery();
  readonly label = computed(() => (this.query.isPending() ? 'a' : 'b'));
}
`,
  'app/src/kinds.spec.ts': `
import { Component, computed } from '@angular/core';
import { injectRowsQuery } from './rows.query';
@Component({ selector: 'app-spec-host', template: '' })
class SpecHost {
  readonly query = injectRowsQuery();
  readonly label = computed(() => (this.query.isPending() ? 'a' : 'b'));
}
`,
  'fleet/src/lib/api/hub/sdk.gen.ts': `
import { Component, computed } from '@angular/core';
import { injectQuery } from '@tanstack/angular-query-experimental';
@Component({ selector: 'gen', template: '' })
export class Generated {
  readonly query = injectQuery(() => ({}));
  readonly label = computed(() => (this.query.isPending() ? 'a' : 'b'));
}
`,
};

/** The exact report the seeded sources must produce. */
const SEEDED_EXPECTED = [
  '  projects/app/src/composing.ts:12 computed viaHelper: ternary via this.helper',
  '  projects/app/src/composing.ts:13 computed viaChain: if via this.second',
  '  projects/app/src/composing.ts:14 computed viaGetter: ternary via this.choice, .some() via this.choice',
  '  projects/app/src/composing.ts:15 computed viaArrow: .filter() via this.arrow',
  '  projects/app/src/kinds.ts:6 computed a: if',
  '  projects/app/src/kinds.ts:7 computed b: switch',
  '  projects/app/src/kinds.ts:8 computed c: ternary',
  '  projects/app/src/kinds.ts:9 computed d: for',
  '  projects/app/src/kinds.ts:10 computed e: for-of',
  '  projects/app/src/kinds.ts:11 computed f: for-in',
  '  projects/app/src/kinds.ts:12 computed g: while',
  '  projects/app/src/kinds.ts:13 computed h: do-while',
  '  projects/app/src/kinds.ts:14 computed i: .filter()',
  '  projects/app/src/kinds.ts:15 computed j: .toSorted()',
  '  projects/app/src/kinds.ts:16 computed k: .map(), .find(), ternary',
  '  projects/app/src/kinds.ts:17 computed l: ternary',
  '  projects/app/src/kinds.ts:18 computed m: .filter(), .map(), .flatMap(), .reduce(), .reduceRight(), .sort(), .toSorted(), .find(), .findIndex(), .findLast(), .findLastIndex(), .some(), .every(), .forEach()',
  '  projects/app/src/wrapped.ts:6 computed first: .find()',
];

/** Prove the sweep against seeded in-memory sources, scoped by the same `inScope` the tree walk
 * uses: every deriving kind flags, a typed `computed<T>()` included, a `this.<member>` reach is
 * followed transitively and terminates, a project-local wrapper makes a container, and
 * composition, event handlers, presentational components, non-component classes, a local or
 * foreign `computed`, specs, and the generated clients all pass (`bzh:case-pins-its-own-name`). */
function assertContainersComposeDetectorWorks() {
  /** @type {Map<string, string>} */
  const sources = new Map();
  for (const [rel, source] of Object.entries(SEEDED_SOURCES)) {
    if (inScope(rel)) sources.set(rel, source.replace(/^\n/, ''));
  }
  const found = derivingComputedLines(sources);
  const missed = SEEDED_EXPECTED.filter((line) => !found.includes(line));
  const extra = found.filter((line) => !SEEDED_EXPECTED.includes(line));
  if (missed.length > 0 || extra.length > 0) {
    throw new Error(
      'containers-compose sweep disagreed with its seeded violations:\n' +
        missed.map((line) => `  missed:${line}`).join('\n') +
        (missed.length > 0 && extra.length > 0 ? '\n' : '') +
        extra.map((line) => `  extra:${line}`).join('\n'),
    );
  }
  const helpers = queryBearingHelpers(new Map([...sources].map(([rel, s]) => [rel, parse(rel, s)])));
  for (const name of ['injectRowsQuery', 'injectRowLookup']) {
    if (!helpers.has(name)) throw new Error(`containers-compose sweep missed the query-bearing helper ${name}`);
  }
  if (helpers.has('injectClock')) throw new Error('containers-compose sweep took injectClock for query-bearing');
}

module.exports = {
  CONTAINERS_COMPOSE_HEADER: HEADER,
  assertContainersComposeDetectorWorks,
  containersComposeViolations,
};
