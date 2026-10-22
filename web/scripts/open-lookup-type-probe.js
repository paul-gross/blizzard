// @ts-check
// Proves the compile-time half of `bzh:frontend-open-vocabulary`: `fleet`'s `openLookup` table and
// a `switch` whose `default` is `unlisted` stay exhaustive over their vocabulary. A table missing a
// value, or a switch with a value no case names, fails to compile; the complete forms compile.
// Vitest never type-checks, so only the compiler can prove this.
const assert = require('node:assert/strict');
const path = require('node:path');
const ts = require('typescript');

const WEB = path.resolve(__dirname, '..');
const HELPER = path.join(WEB, 'projects/fleet/src/lib/core/open-lookup.ts');
const FIXTURE = path.join(WEB, 'projects/fleet/src/lib/core/open-lookup.type-probe.ts');

const HEADER = "import { openLookup, unlisted } from './open-lookup';\ntype Light = 'red' | 'amber' | 'green';\n";

/** Compiles `body` beside the real helper under the workspace's compiler options; its diagnostics' codes.
 * @param {string} body
 * @returns {number[]}
 */
function diagnostics(body) {
  const config = ts.getParsedCommandLineOfConfigFile(path.join(WEB, 'tsconfig.json'), {}, /** @type {any} */ ({
    ...ts.sys,
    onUnRecoverableConfigFileDiagnostic: (/** @type {ts.Diagnostic} */ d) => {
      throw new Error(ts.flattenDiagnosticMessageText(d.messageText, '\n'));
    },
  }));
  assert(config, 'web/tsconfig.json did not parse');
  const options = { ...config.options, noEmit: true };
  const host = ts.createCompilerHost(options);
  const readFile = host.readFile.bind(host);
  host.readFile = (file) => (path.resolve(file) === FIXTURE ? HEADER + body : readFile(file));
  const fileExists = host.fileExists.bind(host);
  host.fileExists = (file) => path.resolve(file) === FIXTURE || fileExists(file);
  const program = ts.createProgram([FIXTURE, HELPER], options, host);
  return [...program.getSemanticDiagnostics(), ...program.getSyntacticDiagnostics()]
    .filter((d) => d.file && path.resolve(d.file.fileName) === FIXTURE)
    .map((d) => d.code);
}

const SWITCH = (cases) =>
  `export function word(light: Light): string {\n  switch (light) {\n${cases}    default:\n      return unlisted(light);\n  }\n}\n`;

function main() {
  const fullTable = "export const L = openLookup<Light, number>({ red: 1, amber: 2, green: 3 }, () => 0);\n";
  assert.deepEqual(diagnostics(fullTable), [], 'a table naming every value must compile');
  assert(diagnostics("export const L = openLookup<Light, number>({ red: 1, green: 3 }, () => 0);\n").length > 0,
    'a table missing a value must fail to compile');

  const all = "    case 'red':\n      return 'stop';\n    case 'amber':\n      return 'slow';\n    case 'green':\n      return 'go';\n";
  assert.deepEqual(diagnostics(SWITCH(all)), [], 'a switch naming every value, defaulting to unlisted, must compile');
  const missing = "    case 'red':\n      return 'stop';\n    case 'green':\n      return 'go';\n";
  assert(diagnostics(SWITCH(missing)).length > 0, 'a switch with a value no case names must fail to compile at unlisted');
  console.log('open-lookup-type-probe: a missing table value or switch case fails to compile, the complete forms compile');
}

main();
