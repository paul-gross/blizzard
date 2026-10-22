// @ts-check
// Proves `web:lint`'s `default-case` rule (`bzh:frontend-open-vocabulary`) refuses a switch with no
// `default` branch — even one ending in a `// no default` comment — and passes one with it, in an app
// file and a fleet file alike.
const assert = require('node:assert/strict');
const path = require('node:path');
const { ESLint } = require('eslint');

const BARE = "export function label(state: string): string {\n  switch (state) {\n    case 'ready':\n      return 'Ready';\n  }\n  return '';\n}\n";
const COMMENTED = "export function label(state: string): string {\n  switch (state) {\n    case 'ready':\n      return 'Ready';\n    // no default\n  }\n  return '';\n}\n";
const DEFAULTED = "export function label(state: string): string {\n  switch (state) {\n    case 'ready':\n      return 'Ready';\n    default:\n      return state;\n  }\n}\n";

async function main() {
  // Rooted at `web/`, so the probe reads the workspace's own config from any working directory.
  const eslint = new ESLint({ cwd: path.resolve(__dirname, '..') });
  for (const filePath of ['projects/hub/src/app/board/board-card/board-card.model.ts', 'projects/fleet/src/lib/core/chunk-lanes.ts']) {
    const [bare] = await eslint.lintText(BARE, { filePath });
    assert(bare.messages.some((message) => message.ruleId === 'default-case'), `a switch with no default must be refused in ${filePath}`);
    const [commented] = await eslint.lintText(COMMENTED, { filePath });
    assert(commented.messages.some((message) => message.ruleId === 'default-case'), `a "no default" comment must not stand in for a default in ${filePath}`);
    const [defaulted] = await eslint.lintText(DEFAULTED, { filePath });
    assert(!defaulted.messages.some((message) => message.ruleId === 'default-case'), `a defaulted switch must pass in ${filePath}`);
  }
  console.log('default-case-lint-probe: a bare or comment-excused switch is refused, a defaulted one passes');
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
