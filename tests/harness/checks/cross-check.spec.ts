import { spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { expect, test } from '@playwright/test';

const script = path.join(__dirname, '..', 'scripts', 'cross-check.ts');

type Status = 'expected' | 'unexpected';

/** A Playwright JSON report holding the named tests, each with its status and the level it declared. */
function report(tests: Record<string, [Status, string]>): string {
  return JSON.stringify({
    suites: [
      {
        title: 'analytics.spec.ts',
        specs: Object.entries(tests).map(([title, [status, level]]) => ({
          title,
          tests: [{ projectName: 'service-hub', status, annotations: [{ type: 'isolation-level', description: level }] }],
        })),
      },
    ],
  });
}

function crossCheck(declared: string | null, raised: string | null): { status: number | null; stderr: string } {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cross-check-'));
  const write = (name: string, content: string | null): string => {
    const file = path.join(dir, name);
    if (content !== null) fs.writeFileSync(file, content);
    return file;
  };
  const run = spawnSync('node', [script, write('declared.json', declared), write('raised.json', raised)], { encoding: 'utf8' });
  return { status: run.status, stderr: run.stderr };
}

test('two reports that agree pass', () => {
  const same = report({ 'reads a chunk': ['expected', 'common'], 'fails everywhere': ['unexpected', 'fresh-tenant'] });
  expect(crossCheck(same, same).status).toBe(0);
});

test('a test that passed in only one run fails, named with its declared level', () => {
  const declared = report({ 'leans on a neighbour': ['expected', 'common'], 'needs the sweep': ['unexpected', 'fresh-tenant'] });
  const raised = report({ 'leans on a neighbour': ['unexpected', 'common'], 'needs the sweep': ['expected', 'fresh-tenant'] });
  const { status, stderr } = crossCheck(declared, raised);
  expect(status).toBe(1);
  expect(stderr).toContain('leans on a neighbour [declared common]');
  expect(stderr).toContain('needs the sweep [declared fresh-tenant]');
});

test('a missing report fails', () => {
  const present = report({ 'reads a chunk': ['expected', 'common'] });
  expect(crossCheck(present, null)).toMatchObject({ status: 1, stderr: expect.stringContaining('report missing') });
  expect(crossCheck(null, present)).toMatchObject({ status: 1, stderr: expect.stringContaining('report missing') });
});
