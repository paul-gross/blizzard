// @ts-check
// Proves `web:lint`'s fleet-root ban (`bzh:frontend-weight-class-entries`): a runtime import, an `import type`, a
// re-export, a dynamic `import()`, and an `import('fleet')` type of the `fleet` root are each refused, and a
// `fleet/<entry>` import passes — under each project's config as `ng lint` reads it, and the exemplars' as
// `exemplar-check` does.
const assert = require('node:assert/strict');
const path = require('node:path');
const { ESLint } = require('eslint');

const RULE = 'fleet-root/no-root-import';
const REFUSED = [
  "import { KitButton } from 'fleet';\nexport const button = KitButton;\n",
  "import type { ChunkSummary } from 'fleet';\nexport type Row = ChunkSummary;\n",
  "export { KitButton } from 'fleet';\n",
  "export * from 'fleet';\n",
  "export const load = () => import('fleet');\n",
  "export type Row = import('fleet').ChunkSummary;\n",
];
const PASSES = [
  "import { KitButton } from 'fleet/kit';\nexport const button = KitButton;\n",
  "export type Row = import('fleet/hub-api').ChunkSummary;\n",
];
const PROJECTS = [
  { config: 'projects/hub/eslint.config.js', file: 'projects/hub/src/app/board/board-page.ts' },
  { config: 'projects/hub/eslint.config.js', file: 'projects/hub/src/app/board/board-page.spec.ts' },
  { config: 'projects/runner/eslint.config.js', file: 'projects/runner/src/app/runners/selftest.ts' },
  { config: 'projects/fleet/eslint.config.js', file: 'projects/fleet/src/lib/host/gate.domain.ts' },
  { config: 'eslint.exemplars.config.js', file: 'hub/src/app/exemplar.ts', cwd: '../exemplars/web' },
];

async function main() {
  // Each config read from the directory its gate runs in (`web/` for `ng lint`, the exemplar tree for
  // `exemplar-check`), whatever the working directory here.
  const web = path.resolve(__dirname, '..');
  for (const project of PROJECTS) {
    const cwd = path.resolve(web, project.cwd ?? '.');
    const eslint = new ESLint({ cwd, overrideConfigFile: path.join(web, project.config) });
    const flagged = async (code) => {
      const [result] = await eslint.lintText(code, { filePath: project.file });
      return result.messages.some((message) => message.ruleId === RULE);
    };
    for (const code of REFUSED) {
      assert(await flagged(code), `${project.file} under ${project.config} must refuse:\n${code}`);
    }
    for (const code of PASSES) {
      assert(!(await flagged(code)), `${project.file} under ${project.config} must accept:\n${code}`);
    }
  }
  console.log('fleet-root-lint-probe: every way of naming the fleet root is refused, and an entry import passes');
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
