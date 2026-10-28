import * as path from 'node:path';
import { expect, test } from '@playwright/test';
import { ESLint } from 'eslint';
import { TESTS_ROOT } from '../../env';

const eslint = new ESLint({ cwd: TESTS_ROOT });

/** The rule messages ESLint reports for `text` linted as the spec file at `file` below `tests/`. */
async function messages(file: string, text: string): Promise<string[]> {
  const [result] = await eslint.lintText(text, { filePath: path.join(TESTS_ROOT, file) });
  return (result?.messages ?? []).map((message) => `${message.ruleId}: ${message.message}`);
}

const FOLDERS = ['service/hub/x.spec.ts', 'service/runner/x.spec.ts', 'service/fleet/x.spec.ts'];

const BANNED_EVERYWHERE: [string, string, RegExp][] = [
  ['node:child_process', `import { spawn } from 'node:child_process';\nvoid spawn;`, /spawns nothing/],
  ['node:http', `import * as http from 'node:http';\nvoid http;`, /harness client/],
  ['node:https', `import * as https from 'node:https';\nvoid https;`, /harness client/],
  ['node:net', `import * as net from 'node:net';\nvoid net;`, /harness client/],
  ['node:sqlite', `import { DatabaseSync } from 'node:sqlite';\nvoid DatabaseSync;`, /public surfaces/],
  ['node:timers', `import { setTimeout as later } from 'node:timers';\nvoid later;`, /never on the clock/],
  ['node:timers/promises', `import { setTimeout as later } from 'node:timers/promises';\nvoid later;`, /never on the clock/],
  ['@playwright/test', `import { test } from '@playwright/test';\nvoid test;`, /harness\/fixtures/],
  ['setTimeout', `setTimeout(() => undefined, 1);`, /never on the clock/],
  ['setInterval', `setInterval(() => undefined, 1);`, /never on the clock/],
];

for (const folder of FOLDERS) {
  for (const [name, text, message] of BANNED_EVERYWHERE) {
    test(`${name} is refused in ${path.dirname(folder)}`, async () => {
      expect((await messages(folder, text)).join('\n')).toMatch(message);
    });
  }
}

for (const folder of ['service/runner/x.spec.ts', 'service/fleet/x.spec.ts']) {
  test(`node:fs is refused in ${path.dirname(folder)}, and node:fs/promises with it`, async () => {
    expect((await messages(folder, `import * as fs from 'node:fs';\nvoid fs;`)).join('\n')).toMatch(/prepares nothing on disk/);
    expect((await messages(folder, `import * as fs from 'node:fs/promises';\nvoid fs;`)).join('\n')).toMatch(/prepares nothing on disk/);
  });
}

test('node:fs stays open to the hub folder', async () => {
  expect(await messages('service/hub/x.spec.ts', `import * as fs from 'node:fs';\nvoid fs;`)).toEqual([]);
});

test('the store folder admits node:sqlite and node:fs, and still refuses the rest', async () => {
  const file = 'service/runner/store/x.spec.ts';
  expect(await messages(file, `import { DatabaseSync } from 'node:sqlite';\nimport * as fs from 'node:fs';\nvoid DatabaseSync;\nvoid fs;`)).toEqual([]);
  expect((await messages(file, `import { spawn } from 'node:child_process';\nvoid spawn;`)).join('\n')).toMatch(/spawns nothing/);
});

test('runnerTest is refused outside the runner folder', async () => {
  const text = `import { runnerTest } from '../../harness/fixtures';\nvoid runnerTest;`;
  for (const file of ['service/hub/x.spec.ts', 'service/fleet/x.spec.ts']) {
    expect((await messages(file, text)).join('\n'), file).toMatch(/runner folder’s world/);
  }
  expect(await messages('service/runner/x.spec.ts', text)).toEqual([]);
});

test('test is refused inside the runner folder, and expect is not', async () => {
  const text = `import { test } from '../../harness/fixtures';\nvoid test;`;
  expect((await messages('service/runner/x.spec.ts', text)).join('\n')).toMatch(/takes `runnerTest`/);
  expect(await messages('service/hub/x.spec.ts', text)).toEqual([]);
  expect(await messages('service/runner/x.spec.ts', `import { expect, runnerTest } from '../../harness/fixtures';\nvoid expect;\nvoid runnerTest;`)).toEqual([]);
});
