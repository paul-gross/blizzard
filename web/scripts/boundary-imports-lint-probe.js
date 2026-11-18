// @ts-check
// Proves `web:lint`'s concept-file restricted-imports blocks: a `*.domain.ts` or `*.translate.ts` refuses Angular
// and the query library — a type-only import included — a `*.form.ts` refuses every Angular import but
// `@angular/forms/signals`, and every file outside `fleet/src/lib/kit/` refuses `@angular/aria`. Pure domain and
// translator files, a Signal Forms form file, and a kit file importing Aria pass — under each project's config the
// way `ng lint` reads it.
const assert = require('node:assert/strict');
const path = require('node:path');
const { ESLint } = require('eslint');

const RULE = '@typescript-eslint/no-restricted-imports';
const ANGULAR = [
  "import { signal } from '@angular/core';\nexport const value = signal(0);\n",
  "import type { Signal } from '@angular/core';\nexport type Value = Signal<number>;\n",
  "import { injectQuery } from '@tanstack/angular-query-experimental';\nexport const inject = injectQuery;\n",
  "import type { CreateQueryResult } from '@tanstack/angular-query-experimental';\nexport type Result = CreateQueryResult<number>;\n",
];
const FORM_REFUSED = [
  "import { FormControl } from '@angular/forms';\nexport const control = new FormControl('');\n",
  "import type { Signal } from '@angular/core';\nexport type Value = Signal<number>;\n",
  "import { signal } from '@angular/core';\nexport const value = signal(0);\n",
  "import { injectQuery } from '@tanstack/angular-query-experimental';\nexport const inject = injectQuery;\n",
];
const FORM_PASSES = "import { form } from '@angular/forms/signals';\nexport const build = form;\n";
const ARIA = [
  "import { Menu } from '@angular/aria/menu';\nexport const menu = Menu;\n",
  "import type { Menu } from '@angular/aria/menu';\nexport type AriaMenu = Menu;\n",
];
const PURE_DOMAIN = "export type Pace = 'fast' | 'slow';\nexport function isFast(pace: Pace): boolean {\n  return pace === 'fast';\n}\n";
const PURE_TRANSLATE = "import type { ChunkSummary } from 'fleet';\nexport function ids(chunks: readonly ChunkSummary[]): string[] {\n  return chunks.map((chunk) => chunk.chunk_id);\n}\n";
const KIT = 'projects/fleet/src/lib/kit/kit-menu.ts';
const PROJECTS = [
  { config: 'projects/hub/eslint.config.js', dir: 'projects/hub/src/app/board' },
  { config: 'projects/runner/eslint.config.js', dir: 'projects/runner/src/app/runners' },
  { config: 'projects/fleet/eslint.config.js', dir: 'projects/fleet/src/lib/host' },
];

async function main() {
  // Rooted at `web/` with each project's own config, as `ng lint` runs, from any working directory.
  const cwd = path.resolve(__dirname, '..');
  for (const project of PROJECTS) {
    const eslint = new ESLint({ cwd, overrideConfigFile: path.join(cwd, project.config) });
    const flagged = async (code, filePath) => {
      const [result] = await eslint.lintText(code, { filePath });
      return result.messages.some((message) => message.ruleId === RULE);
    };
    const under = `under ${project.config}`;
    const domain = `${project.dir}/pace.domain.ts`;
    const translate = `${project.dir}/pace.translate.ts`;
    const form = `${project.dir}/pace.form.ts`;
    const model = `${project.dir}/pace.model.ts`;
    const plain = `${project.dir}/pace.ts`;
    for (const code of ANGULAR) {
      assert(await flagged(code, domain), `${domain} ${under} must refuse:\n${code}`);
      assert(await flagged(code, translate), `${translate} ${under} must refuse:\n${code}`);
    }
    for (const code of FORM_REFUSED) {
      assert(await flagged(code, form), `${form} ${under} must refuse:\n${code}`);
    }
    assert(!(await flagged(FORM_PASSES, form)), `${form} ${under} must accept @angular/forms/signals`);
    for (const code of ARIA) {
      for (const file of [plain, domain, translate, form, model]) {
        assert(await flagged(code, file), `${file} ${under} must refuse:\n${code}`);
      }
      assert(!(await flagged(code, KIT)), `${KIT} ${under} must accept:\n${code}`);
    }
    assert(!(await flagged(PURE_DOMAIN, domain)), `a pure domain file must pass ${under}`);
    assert(!(await flagged(PURE_TRANSLATE, translate)), `a pure translator must pass ${under}`);
  }
  console.log('boundary-imports-lint-probe: impure domain, translator, and form files and Aria outside the kit are refused; pure concept files, a Signal Forms form, and a kit Aria import pass');
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
