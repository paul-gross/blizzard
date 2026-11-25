// @ts-check
// Retirement inventory: identity is the declaration's path AND name, not the spelling alone.
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const { createProgram, walkTs } = require('./placement-sweep');

const ROOT = path.resolve(__dirname, '..');
const INVENTORY = {
  'hub/src/app/core/projects.query.ts': ['projectsQueryOptions', 'injectProjectsQuery'],
  'hub/src/app/core/chunks.query.ts': ['injectHubBoardChunksQuery'],
  'hub/src/app/core/runners.query.ts': ['injectHubRunnersQuery'],
  'hub/src/app/admin/forges/forges.query.ts': ['injectForgesQuery', 'injectForgeQuery'],
  'hub/src/app/board/questions/questions.query.ts': ['injectHubQuestionsQuery'],
  'hub/src/app/board/gates/gates.query.ts': ['injectHubDecisionsQuery'],
  'hub/src/app/board/fleet-spend/fleet-spend.query.ts': ['injectHubFleetSpendQuery'],
};
const RETIRED_NAMES = new Set(Object.values(INVENTORY).flat());
const RETIRED_RULE = /\bbzh:frontend-wire-conformist\b/g;

function census(root, files, memory = null) {
  const program = createProgram(root, files, memory);
  const checker = program.getTypeChecker();
  const lines = [];
  // Track public names syntactically as well as by symbol origin. Once the original
  // declaration is gone the compiler cannot resolve its aliases, but the old barrel
  // chain and its consumers still need to be retired.
  const exports = new Map();
  const namespaces = new Map();
  for (const [rel, names] of Object.entries(INVENTORY)) {
    exports.set(path.join(root, 'projects', rel), new Set(names));
  }
  function moduleFile(source, specifier) {
    if (!ts.isStringLiteral(specifier) || !specifier.text.startsWith('.')) return null;
    const base = path.resolve(path.dirname(source.fileName), specifier.text);
    return [base, base + '.ts', path.join(base, 'index.ts')].find((file) =>
      program.getSourceFile(file) || exports.has(file)) || null;
  }
  let changed = true;
  while (changed) {
    changed = false;
    for (const file of files) {
      const source = program.getSourceFile(file);
      if (!source) continue;
      const local = new Map();
      for (const statement of source.statements) {
        if (!ts.isImportDeclaration(statement) || !statement.importClause?.namedBindings ||
            !ts.isNamedImports(statement.importClause.namedBindings)) continue;
        const target = moduleFile(source, statement.moduleSpecifier);
        const upstream = target && exports.get(target);
        for (const element of statement.importClause.namedBindings.elements) {
          if (upstream?.has((element.propertyName || element.name).text)) local.set(element.name.text, true);
        }
      }
      for (const statement of source.statements) {
        if (ts.isExportDeclaration(statement) && !statement.moduleSpecifier && statement.exportClause &&
            ts.isNamedExports(statement.exportClause)) {
          if (!exports.has(file)) exports.set(file, new Set());
          for (const element of statement.exportClause.elements) {
            if (local.has((element.propertyName || element.name).text) && !exports.get(file).has(element.name.text)) {
              exports.get(file).add(element.name.text); changed = true;
            }
          }
        }
        if (!ts.isExportDeclaration(statement) || !statement.moduleSpecifier) continue;
        const target = moduleFile(source, statement.moduleSpecifier);
        const upstream = target && exports.get(target);
        const upstreamNamespaces = target && namespaces.get(target);
        if (!upstream && !upstreamNamespaces) continue;
        if (!namespaces.has(file)) namespaces.set(file, new Map());
        const currentNamespaces = namespaces.get(file);
        if (statement.exportClause && ts.isNamespaceExport(statement.exportClause)) {
          if (!currentNamespaces.has(statement.exportClause.name.text)) {
            currentNamespaces.set(statement.exportClause.name.text, target); changed = true;
          }
        } else if (!statement.exportClause && upstreamNamespaces) {
          for (const [name, origin] of upstreamNamespaces) {
            if (!currentNamespaces.has(name)) { currentNamespaces.set(name, origin); changed = true; }
          }
        } else if (statement.exportClause && ts.isNamedExports(statement.exportClause) && upstreamNamespaces) {
          for (const element of statement.exportClause.elements) {
            const origin = upstreamNamespaces.get((element.propertyName || element.name).text);
            if (origin && !currentNamespaces.has(element.name.text)) {
              currentNamespaces.set(element.name.text, origin); changed = true;
            }
          }
        }
        const names = statement.exportClause && ts.isNamedExports(statement.exportClause)
          ? statement.exportClause.elements.filter((element) => upstream?.has((element.propertyName || element.name).text)).map((element) => element.name.text)
          : !statement.exportClause ? [...(upstream || [])] : [];
        if (!exports.has(file)) exports.set(file, new Set());
        for (const name of names) if (!exports.get(file).has(name)) { exports.get(file).add(name); changed = true; }
      }
    }
  }
  for (const file of files) {
    const source = program.getSourceFile(file);
    if (!source) continue;
    function retired(symbol) {
      const seen = new Set();
      while (symbol && symbol.flags & ts.SymbolFlags.Alias && !seen.has(symbol)) {
        seen.add(symbol);
        symbol = checker.getAliasedSymbol(symbol);
      }
      const target = symbol?.declarations?.find((declaration) => {
        const rel = path.relative(path.join(root, 'projects'), declaration.getSourceFile().fileName).split(path.sep).join('/');
        return INVENTORY[rel]?.includes(symbol.name);
      });
      return target ? symbol : undefined;
    }
    const taintedImports = new Map();
    const namespaceImports = new Map();
    for (const statement of source.statements) {
      if (!ts.isImportDeclaration(statement) || !statement.importClause?.namedBindings) continue;
      const bindings = statement.importClause.namedBindings;
      const target = moduleFile(source, statement.moduleSpecifier);
      if (!target) continue;
      if (ts.isNamespaceImport(bindings)) {
        namespaceImports.set(checker.getSymbolAtLocation(bindings.name), target);
      } else if (ts.isNamedImports(bindings)) {
        for (const element of bindings.elements) {
          const origin = namespaces.get(target)?.get((element.propertyName || element.name).text);
          if (origin) namespaceImports.set(checker.getSymbolAtLocation(element.name), origin);
        }
      }
    }
    for (const statement of source.statements) {
      if (!ts.isImportDeclaration(statement) || !statement.importClause?.namedBindings ||
          !ts.isNamedImports(statement.importClause.namedBindings)) continue;
      const target = moduleFile(source, statement.moduleSpecifier);
      const upstream = target && exports.get(target);
      for (const element of statement.importClause.namedBindings.elements) {
        const name = (element.propertyName || element.name).text;
        if (upstream?.has(name)) {
          const local = checker.getSymbolAtLocation(element.name);
          if (local) taintedImports.set(local, name);
        }
      }
    }
    function visit(node) {
      if (ts.isPropertyAccessExpression(node) || (ts.isElementAccessExpression(node) && ts.isStringLiteral(node.argumentExpression)) || ts.isQualifiedName(node)) {
        const parts = [];
        let cursor = node;
        while (ts.isPropertyAccessExpression(cursor) || ts.isElementAccessExpression(cursor) || ts.isQualifiedName(cursor)) {
          if (ts.isPropertyAccessExpression(cursor)) { parts.unshift(cursor.name.text); cursor = cursor.expression; }
          else if (ts.isQualifiedName(cursor)) { parts.unshift(cursor.right.text); cursor = cursor.left; }
          else if (ts.isStringLiteral(cursor.argumentExpression)) { parts.unshift(cursor.argumentExpression.text); cursor = cursor.expression; }
          else break;
        }
        let origin = ts.isIdentifier(cursor) && namespaceImports.get(checker.getSymbolAtLocation(cursor));
        for (const part of parts) {
          if (!origin) break;
          if (exports.get(origin)?.has(part)) {
            const position = source.getLineAndCharacterOfPosition(node.getStart(source));
            lines.push(`${path.relative(root, file)}:${position.line + 1}: retired namespace reference ${part}`);
            break;
          }
          origin = namespaces.get(origin)?.get(part);
        }
      }
      if (ts.isExportDeclaration(node) && node.moduleSpecifier) {
        const target = moduleFile(source, node.moduleSpecifier);
        const upstream = target && exports.get(target);
        if (upstream && (!node.exportClause || ts.isNamespaceExport(node.exportClause) || (ts.isNamedExports(node.exportClause) &&
            node.exportClause.elements.some((element) => upstream?.has((element.propertyName || element.name).text))))) {
          const position = source.getLineAndCharacterOfPosition(node.getStart(source));
          lines.push(`${path.relative(root, file)}:${position.line + 1}: retired compatibility export`);
        }
      }
      if (ts.isExportDeclaration(node) && !node.moduleSpecifier && node.exportClause && ts.isNamedExports(node.exportClause)) {
        for (const element of node.exportClause.elements) {
          const local = checker.getExportSpecifierLocalTargetSymbol(element);
          if (taintedImports.has(local)) {
            const position = source.getLineAndCharacterOfPosition(element.getStart(source));
            lines.push(`${path.relative(root, file)}:${position.line + 1}: retired compatibility export`);
          }
        }
      }
      if (ts.isIdentifier(node)) {
        const name = taintedImports.get(checker.getSymbolAtLocation(node));
        if (name) {
          const position = source.getLineAndCharacterOfPosition(node.getStart(source));
          lines.push(`${path.relative(root, file)}:${position.line + 1}: retired reference ${name}`);
        }
      }
      // Still report a dangling direct import/export after its declaration is removed.
      if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier &&
          ts.isStringLiteral(node.moduleSpecifier) && node.moduleSpecifier.text.startsWith('.')) {
        const resolved = ts.resolveModuleName(node.moduleSpecifier.text, file, program.getCompilerOptions(), ts.sys).resolvedModule;
        const rel = resolved && path.relative(path.join(root, 'projects'), resolved.resolvedFileName).split(path.sep).join('/');
        const names = rel && INVENTORY[rel];
        const bindings = ts.isImportDeclaration(node) ? node.importClause?.namedBindings : node.exportClause;
        if (names && bindings && (ts.isNamedImports(bindings) || ts.isNamedExports(bindings))) {
          for (const element of bindings.elements) {
            const name = (element.propertyName || element.name).text;
            if (names.includes(name) && !retired(checker.getSymbolAtLocation(element.name))) {
              const position = source.getLineAndCharacterOfPosition(element.getStart(source));
              lines.push(`${path.relative(root, file)}:${position.line + 1}: retired ${name}`);
            }
          }
        }
      }
      if (ts.isExportDeclaration(node) && !node.exportClause && node.moduleSpecifier) {
        const module = checker.getSymbolAtLocation(node.moduleSpecifier);
        for (const exported of module ? checker.getExportsOfModule(module) : []) {
          const symbol = retired(exported);
          if (symbol) {
            const position = source.getLineAndCharacterOfPosition(node.getStart(source));
            lines.push(`${path.relative(root, file)}:${position.line + 1}: retired ${symbol.name}`);
          }
        }
      }
      if (ts.isIdentifier(node)) {
        const symbol = retired(checker.getSymbolAtLocation(node));
        // This shared cache primitive may remain private in its defining module.
        const privatePrimitive = symbol?.name === 'projectsQueryOptions' &&
          source.fileName === symbol.declarations?.[0]?.getSourceFile().fileName &&
          !ts.isExportSpecifier(node.parent) &&
          !symbol.declarations?.some((declaration) => ts.canHaveModifiers(declaration) &&
            ts.getModifiers(declaration)?.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword));
        if (symbol && !privatePrimitive) {
          const position = source.getLineAndCharacterOfPosition(node.getStart(source));
          lines.push(`${path.relative(root, file)}:${position.line + 1}: retired ${symbol.name}`);
        }
      }
      ts.forEachChild(node, visit);
    }
    visit(source);
  }
  return [...new Set(lines)];
}

function citationLines(file, text) {
  const lines = [];
  RETIRED_RULE.lastIndex = 0;
  for (const match of text.matchAll(RETIRED_RULE)) {
    lines.push(`${file}:${text.slice(0, match.index).split('\n').length}: retired rule citation`);
  }
  return lines;
}

// Prose has no compiler symbol identity: a bare inventoried API name is a
// retirement reference. A qualified domain/generated reference is not that API.
function proseLines(file, text) {
  const lines = [];
  const pattern = /[A-Za-z_$][\w$]*/g;
  for (const [index, line] of text.split('\n').entries()) {
    for (const match of line.matchAll(pattern)) {
      if (!RETIRED_NAMES.has(match[0])) continue;
      const before = line.slice(0, match.index);
      if (/(?:[\w/.-]+\.domain|[\w/.-]+\.gen)(?:\.ts)?[#:]$/.test(before)) continue;
      lines.push(`${file}:${index + 1}: retired API reference ${match[0]}`);
    }
  }
  return [...new Set(lines)];
}

function sourceProse(file, text) {
  if (!file.endsWith('.ts')) return proseLines(file, text);
  const scanner = ts.createScanner(ts.ScriptTarget.Latest, false, ts.LanguageVariant.Standard, text);
  const lines = [];
  for (let token = scanner.scan(); token !== ts.SyntaxKind.EndOfFileToken; token = scanner.scan()) {
    if (token !== ts.SyntaxKind.SingleLineCommentTrivia && token !== ts.SyntaxKind.MultiLineCommentTrivia) continue;
    const startLine = text.slice(0, scanner.getTokenPos()).split('\n').length;
    for (const entry of proseLines(file, scanner.getTokenText())) {
      lines.push(entry.replace(`${file}:`, `${file}:`).replace(/:(\d+):/, (_, line) => `:${startLine + Number(line) - 1}:`));
    }
  }
  return lines;
}

function assertRetirementCensusWorks() {
  const root = '/retirement-fixture';
  const base = `${root}/projects/hub/src/app`;
  const memory = new Map(Object.entries({
    [`${base}/core/projects.query.ts`]: 'export function injectProjectsQuery() {}',
    [`${base}/barrel.ts`]: "export { injectProjectsQuery as old } from './core/projects.query';",
    [`${base}/nested.ts`]: "export { old as renamed } from './barrel';",
    [`${base}/consumer.ts`]: "import { renamed as useOld } from './nested'; useOld();",
    [`${base}/local.ts`]: "import { renamed as x } from './nested'; export { x as final };",
    [`${base}/final.ts`]: "import { final as f } from './local'; f();",
    [`${base}/legal.ts`]: 'export function injectProjectsQuery() {}',
    [`${base}/private.translate.ts`]: 'const wire = {}; export { wire };',
    [`${base}/transport.ts`]: 'export const response = {};',
    [`${base}/star.ts`]: "export * from './nested';",
    [`${base}/ns-consumer.ts`]: "import * as api from './star'; api.renamed();",
    [`${base}/ns-barrel.ts`]: "export * as legacy from './nested';",
    [`${base}/ns-nested.ts`]: "export { legacy as layer } from './ns-barrel';",
    [`${base}/ns-use.ts`]: "import * as outer from './ns-nested'; outer.layer.renamed();",
    [`${base}/ns-named.ts`]: "import { layer as x } from './ns-nested'; x.renamed();",
    [`${base}/ns-bracket.ts`]: "import * as api from './star'; api['renamed']();",
    [`${base}/ns-type.ts`]: "import * as api from './star'; type T = typeof api.renamed;",
    [`${base}/legal-ns.ts`]: "import * as domain from './legal'; domain.injectProjectsQuery();",
    [`${base}/generated.gen.ts`]: 'export function injectProjectsQuery() {}',
    [`${base}/legal-generated.ts`]: "import * as generated from './generated.gen'; generated.injectProjectsQuery();",

  }));
  const result = census(root, [...memory.keys()], memory).join('\n');
  for (const name of ['projects.query.ts', 'barrel.ts', 'nested.ts', 'consumer.ts', 'star.ts', 'local.ts', 'final.ts', 'ns-consumer.ts', 'ns-barrel.ts', 'ns-use.ts', 'ns-named.ts', 'ns-bracket.ts', 'ns-type.ts']) {
    if (!result.includes(name)) throw new Error(`retirement census missed ${name}`);
  }
  for (const name of ['legal.ts', 'private.translate.ts', 'transport.ts', 'legal-ns.ts', 'legal-generated.ts', 'generated.gen.ts']) {
    if (result.includes(name)) throw new Error(`retirement census falsely reported ${name}`);
  }
  memory.set(`${base}/core/projects.query.ts`, 'export const unrelated = 1;');
  const dangling = census(root, [...memory.keys()], memory).join('\n');
  for (const name of ['barrel.ts', 'nested.ts', 'consumer.ts', 'star.ts', 'local.ts', 'final.ts', 'ns-consumer.ts', 'ns-barrel.ts', 'ns-use.ts', 'ns-named.ts', 'ns-bracket.ts', 'ns-type.ts']) {
    if (!dangling.includes(name)) throw new Error(`retirement census missed dangling ${name}`);
  }
  for (const name of RETIRED_NAMES) {
    if (!proseLines('guide.md', `Use \`${name}\` instead`).length) throw new Error(`prose census missed ${name}`);
    if (proseLines('guide.md', `concept.domain.ts#${name} api/types.gen.ts#${name}`).length) {
      throw new Error(`prose census rejected qualified domain/generated ${name}`);
    }
  }
  if (!sourceProse('component.ts', '// invoke injectHubQuestionsQuery here').length ||
      sourceProse('component.ts', 'const injectHubQuestionsQuery = 1;').length ||
      !sourceProse('web/scripts/example.js', 'injectHubQuestionsQuery();').length) {
    throw new Error('source prose census failed');
  }
  if (citationLines('doc', 'see bzh:frontend-wire-conformist').length !== 1 || citationLines('doc', 'bzh:frontend-domain-boundary').length) {
    throw new Error('retirement citation census failed');
  }
}

function walk(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) return ['node_modules', 'dist', 'build', '.git', '.angular', '.venv', '__pycache__', '.pytest_cache', '.ruff_cache', '.hypothesis', '.vitest-attachments'].includes(entry.name) ? [] : walk(file);
    return /\.(?:ts|js|mjs|cjs|html|css|md|py|sh|ya?ml|toml)$/.test(file) && !/\.gen\.(?:ts|js)$/.test(file) && file !== __filename ? [file] : [];
  });
}
function retirementViolations() {
  const repo = path.dirname(ROOT);
  const files = walkTs(path.join(ROOT, 'projects'));
  const citations = ['README.md', 'SECURITY.md', 'docs', 'exemplars', 'src', 'scripts', 'tests', 'web'].flatMap((name) => {
    const target = path.join(repo, name);
    return fs.existsSync(target) && fs.statSync(target).isFile() ? [target] : walk(target);
  });
  return [...census(ROOT, files), ...citations.flatMap((file) => {
    const rel = path.relative(repo, file);
    const text = fs.readFileSync(file, 'utf8');
    return [...citationLines(rel, text), ...sourceProse(rel, text)];
  })];
}
if (require.main === module) {
  assertRetirementCensusWorks();
  if (process.argv.includes('--check')) {
    const lines = retirementViolations();
    if (lines.length) { console.error('retirement census violations:\n' + lines.join('\n')); process.exitCode = 1; }
    else console.log('retirement census clean');
  } else console.log('retirement census self-tests passed');
}
module.exports = { assertRetirementCensusWorks, retirementViolations, census, proseLines };
