// @ts-check
// Proves `web:lint`'s restricted-imports rule (`bzh:frontend-containers-compose`) refuses a `*.model.ts`
// that imports Angular or the query library — a type-only import included — and passes a pure model and a
// non-model file that imports Angular, under each project's config the way `ng lint` reads it.
const assert = require('node:assert/strict');
const path = require('node:path');
const { ESLint } = require('eslint');

const RULE = '@typescript-eslint/no-restricted-imports';
const REFUSED = [
  "import { signal } from '@angular/core';\nexport const value = signal(0);\n",
  "import type { Signal } from '@angular/core';\nexport type Value = Signal<number>;\n",
  "import { moveItemInArray } from '@angular/cdk/drag-drop';\nexport const move = moveItemInArray;\n",
  "import type { CdkDragDrop } from '@angular/cdk/drag-drop';\nexport type Drop = CdkDragDrop<string>;\n",
  "import { injectQuery } from '@tanstack/angular-query-experimental';\nexport const inject = injectQuery;\n",
  "import type { CreateQueryResult } from '@tanstack/angular-query-experimental';\nexport type Result = CreateQueryResult<number>;\n",
];
const PURE = "import type { ChunkSummary } from 'fleet';\nexport function ids(chunks: readonly ChunkSummary[]): string[] {\n  return chunks.map((chunk) => chunk.chunk_id);\n}\n";
const PROJECTS = [
  { config: 'projects/hub/eslint.config.js', model: 'projects/hub/src/app/board/board-page.model.ts', other: 'projects/hub/src/app/board/board-page.ts' },
  { config: 'projects/runner/eslint.config.js', model: 'projects/runner/src/app/runners/selftest.model.ts', other: 'projects/runner/src/app/runners/selftest.ts' },
  { config: 'projects/fleet/eslint.config.js', model: 'projects/fleet/src/lib/host/subscription-pace.model.ts', other: 'projects/fleet/src/lib/host/subscription-pace.ts' },
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
    for (const code of REFUSED) {
      assert(await flagged(code, project.model), `${project.model} under ${project.config} must refuse:\n${code}`);
      assert(!(await flagged(code, project.other)), `${project.other} under ${project.config} must accept:\n${code}`);
    }
    assert(!(await flagged(PURE, project.model)), `a pure model must pass under ${project.config}`);
  }
  console.log('model-imports-lint-probe: a model importing Angular or the query library is refused, a pure model and a non-model file pass');
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
