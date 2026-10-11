// @ts-check
/**
 * Puts every in-app link under the project lens, folder by folder — a deterministic rewrite, so running it again over
 * the same tree changes nothing, and the same run over the same tree writes the same bytes.
 *
 *   node scripts/lens-links.mjs <folder>...    (from web/; folders relative to it, e.g. projects/hub/src/app/board)
 *
 * Templates (`*.html`, and a component's inline `template:`):
 *   - `routerLink="/view/…"` becomes `[routerLink]="'/view/…' | lensLink"`; a public page (`/login`, `/invite…`) and
 *     a link already under a lens (`/p/…`) are left as they are;
 *   - every `[routerLink]="expr"` not already through the pipe becomes `[routerLink]="expr | lensLink"` — the pipe
 *     leaves a relative link alone, so the rule needs no guess at what `expr` holds;
 *   - the component that owns a rewritten template imports `LensLink`.
 * Classes (`*.ts`): the commands of every `….router.navigate(commands, …)`, and a literal absolute URL handed to
 * `….router.navigateByUrl`, pass through `this.lens.link(…)`; the class injects `ProjectLens` as `lens`. A navigation
 * outside a class is reported, never rewritten. Specs and the lens's own file are skipped.
 *
 * The lens itself is `projects/hub/src/app/core/project-lens.ts` (`ProjectLens.link`) and the pipe
 * `projects/fleet/src/lib/core/lens-link.ts` (`LensLink`, through `IN_APP_LINK`).
 */
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const LENS_FILE = join(WEB, 'projects/hub/src/app/core/project-lens.ts');
const PIPE_FILE = join(WEB, 'projects/fleet/src/lib/core/lens-link.ts');
const HUB = join(WEB, 'projects/hub') + sep;
const UNLENSED_ROOTS = new Set(['p', 'login', 'invite']);

/** @param {string} dir @param {string[]} found */
function walk(dir, found = []) {
  for (const name of readdirSync(dir).sort()) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path, found);
    else if ((name.endsWith('.ts') || name.endsWith('.html')) && !name.endsWith('.spec.ts')) found.push(path);
  }
  return found;
}

/** @param {string} path */
function lensable(path) {
  const root = path.split(/[/?#]/).filter((segment) => segment !== '')[0];
  return root === undefined || !UNLENSED_ROOTS.has(root);
}

/** The template with its links under the lens. @param {string} html */
export function rewriteTemplate(html) {
  return html
    .replace(/(\s)routerLink="(\/[^"]*)"/g, (whole, space, path) =>
      lensable(path) ? `${space}[routerLink]="'${path}' | lensLink"` : whole,
    )
    .replace(/\[routerLink\]="([^"]*)"/g, (whole, expr) =>
      /\|\s*lensLink\b/.test(expr) ? whole : `[routerLink]="${expr.trim()} | lensLink"`,
    );
}

/** A module specifier for `target` as `fromFile` imports it. @param {string} fromFile @param {string} target */
function specifier(fromFile, target) {
  const path = relative(dirname(fromFile), target).split(sep).join('/').replace(/\.ts$/, '');
  return path.startsWith('.') ? path : `./${path}`;
}

/**
 * Text edits, applied back to front so earlier offsets stay valid. @typedef {{ start: number, end: number, text: string }} Edit
 * @param {string} text @param {Edit[]} edits
 */
function apply(text, edits) {
  let out = text;
  for (const edit of [...edits].sort((a, b) => b.start - a.start || b.end - a.end)) {
    out = out.slice(0, edit.start) + edit.text + out.slice(edit.end);
  }
  return out;
}

/** Adds `name` to an import from `from` — into an existing named import of that module, else as a new declaration
 * after the last import. @param {ts.SourceFile} source @param {string} name @param {string} from @returns {Edit[]} */
function ensureImport(source, name, from) {
  const imports = source.statements.filter(ts.isImportDeclaration);
  for (const decl of imports) {
    if (!ts.isStringLiteral(decl.moduleSpecifier) || decl.moduleSpecifier.text !== from) continue;
    const bindings = decl.importClause?.namedBindings;
    if (decl.importClause?.isTypeOnly || !bindings || !ts.isNamedImports(bindings)) continue;
    if (bindings.elements.some((element) => element.name.text === name)) return [];
    const last = bindings.elements[bindings.elements.length - 1];
    return [{ start: last.end, end: last.end, text: `, ${name}` }];
  }
  const anchor = imports.length > 0 ? imports[imports.length - 1].end : 0;
  return [{ start: anchor, end: anchor, text: `\nimport { ${name} } from '${from}';` }];
}

/** The `@Component({...})` argument of `cls`, if it has one. @param {ts.ClassDeclaration} cls */
function componentMeta(cls) {
  for (const decorator of ts.getDecorators(cls) ?? []) {
    const call = decorator.expression;
    if (!ts.isCallExpression(call) || !ts.isIdentifier(call.expression) || call.expression.text !== 'Component') continue;
    const [arg] = call.arguments;
    if (arg && ts.isObjectLiteralExpression(arg)) return arg;
  }
  return undefined;
}

/** @param {ts.ObjectLiteralExpression} meta @param {string} key */
function property(meta, key) {
  return meta.properties.find(
    (p) => ts.isPropertyAssignment(p) && (ts.isIdentifier(p.name) || ts.isStringLiteral(p.name)) && p.name.text === key,
  );
}

/** Edits that make the component's `imports` hold `LensLink`. @param {ts.ObjectLiteralExpression} meta @returns {Edit[]} */
function importPipe(meta) {
  const imports = property(meta, 'imports');
  if (imports && ts.isPropertyAssignment(imports) && ts.isArrayLiteralExpression(imports.initializer)) {
    const list = imports.initializer;
    if (list.elements.some((element) => ts.isIdentifier(element) && element.text === 'LensLink')) return [];
    if (list.elements.length === 0) return [{ start: list.end - 1, end: list.end - 1, text: 'LensLink' }];
    const last = list.elements[list.elements.length - 1];
    return [{ start: last.end, end: last.end, text: ', LensLink' }];
  }
  const first = meta.properties[0];
  return [{ start: first.end, end: first.end, text: ',\n  imports: [LensLink]' }];
}

/** Whether `expr` is `<something>.router` or `router`. @param {ts.Expression} expr */
function isRouter(expr) {
  if (ts.isIdentifier(expr)) return expr.text === 'router';
  return ts.isPropertyAccessExpression(expr) && expr.name.text === 'router';
}

/** @param {ts.Expression} expr */
function isLensed(expr) {
  return (
    ts.isCallExpression(expr) &&
    ts.isPropertyAccessExpression(expr.expression) &&
    expr.expression.name.text === 'link' &&
    ts.isPropertyAccessExpression(expr.expression.expression) &&
    expr.expression.expression.name.text === 'lens'
  );
}

/** @param {string} file @param {string} text @param {string[]} report */
export function rewriteSource(file, text, report) {
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  /** @type {Edit[]} */
  const edits = [];
  let needsPipe = false;
  let needsLens = false;

  for (const statement of source.statements) {
    if (!ts.isClassDeclaration(statement)) continue;
    const cls = statement;
    const meta = componentMeta(cls);
    let pipeHere = false;

    // The template: inline, or the sibling file this pass rewrites on its own (see `run`).
    const inline = meta && property(meta, 'template');
    if (inline && ts.isPropertyAssignment(inline)) {
      const init = inline.initializer;
      if (ts.isNoSubstitutionTemplateLiteral(init) || ts.isStringLiteral(init)) {
        const body = init.getText(source);
        const next = rewriteTemplate(body);
        if (next !== body) {
          edits.push({ start: init.getStart(source), end: init.end, text: next });
          pipeHere = true;
        }
      }
    }
    const url = meta && property(meta, 'templateUrl');
    if (url && ts.isPropertyAssignment(url) && ts.isStringLiteral(url.initializer)) {
      const html = join(dirname(file), url.initializer.text);
      const before = readFileSync(html, 'utf8');
      if (rewriteTemplate(before) !== before || /\|\s*lensLink\b/.test(before)) pipeHere = true;
    }
    if (pipeHere && meta) {
      edits.push(...importPipe(meta));
      needsPipe = true;
    }

    // Navigations in the class's members.
    let lensHere = false;
    /** @param {ts.Node} node */
    const visit = (node) => {
      if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression) && isRouter(node.expression.expression)) {
        const verb = node.expression.name.text;
        const [first] = node.arguments;
        const literalUrl =
          first && (ts.isStringLiteral(first) || ts.isNoSubstitutionTemplateLiteral(first)) && first.text.startsWith('/');
        if (first && !isLensed(first) && (verb === 'navigate' || (verb === 'navigateByUrl' && literalUrl))) {
          if (verb === 'navigateByUrl' && !lensable(first.text)) return;
          edits.push({ start: first.getStart(source), end: first.getStart(source), text: 'this.lens.link(' });
          edits.push({ start: first.end, end: first.end, text: ')' });
          lensHere = true;
        }
      }
      ts.forEachChild(node, visit);
    };
    for (const member of cls.members) visit(member);
    const hasLens = cls.members.some(
      (member) => ts.isPropertyDeclaration(member) && ts.isIdentifier(member.name) && member.name.text === 'lens',
    );
    if (lensHere && !hasLens) {
      const open = cls.members.pos;
      edits.push({ start: open, end: open, text: '\n  private readonly lens = inject(ProjectLens);\n' });
      needsLens = true;
    }
  }

  // Navigations outside any class are reported for a person to place.
  /** @param {ts.Node} node @param {boolean} inClass */
  const scan = (node, inClass) => {
    const here = inClass || ts.isClassDeclaration(node);
    if (!here && ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)) {
      const verb = node.expression.name.text;
      if ((verb === 'navigate' || verb === 'navigateByUrl' || verb === 'createUrlTree') && isRouter(node.expression.expression)) {
        const { line } = source.getLineAndCharacterOfPosition(node.getStart(source));
        report.push(`${relative(WEB, file)}:${line + 1}: ${verb} outside a class, left as it is`);
      }
    }
    ts.forEachChild(node, (child) => scan(child, here));
  };
  scan(source, false);

  if (needsPipe) edits.push(...ensureImport(source, 'LensLink', file.startsWith(HUB) ? 'fleet/shell' : specifier(file, PIPE_FILE)));
  if (needsLens) {
    edits.push(...ensureImport(source, 'inject', '@angular/core'));
    edits.push(...ensureImport(source, 'ProjectLens', specifier(file, LENS_FILE)));
  }
  return apply(text, edits);
}

/** @param {string[]} folders */
function run(folders) {
  /** @type {string[]} */
  const report = [];
  /** @type {string[]} */
  const changed = [];
  const files = folders.flatMap((folder) => walk(resolve(WEB, folder)));
  // Classes first: a component learns whether its template file needs the pipe from the file as it stands.
  for (const file of files.filter((f) => f.endsWith('.ts') && f !== LENS_FILE && f !== PIPE_FILE)) {
    const before = readFileSync(file, 'utf8');
    const after = rewriteSource(file, before, report);
    if (after !== before) {
      writeFileSync(file, after);
      changed.push(file);
    }
  }
  for (const file of files.filter((f) => f.endsWith('.html'))) {
    const before = readFileSync(file, 'utf8');
    const after = rewriteTemplate(before);
    if (after !== before) {
      writeFileSync(file, after);
      changed.push(file);
    }
  }
  for (const file of changed.sort()) console.log(`rewrote ${relative(WEB, file)}`);
  for (const line of report) console.log(line);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const folders = process.argv.slice(2);
  if (folders.length === 0) {
    console.error('usage: node scripts/lens-links.mjs <folder>...');
    process.exit(2);
  }
  run(folders);
}
