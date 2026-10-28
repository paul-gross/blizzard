import * as fs from 'node:fs';
import * as path from 'node:path';
import { parse } from 'smol-toml';
import { expect, runnerTest as test } from '../../../fixtures';

test('a runner.toml beside the spec expands the minted world’s paths into the host file', async ({ runnerHost }) => {
  const file = parse(fs.readFileSync(path.join(runnerHost.dir, 'blizzard-host.toml'), 'utf8')) as {
    workspace: { id: string; root: string; prompt: string }[];
    runner: { runner_prompt: string }[];
  };
  const workspace = file.workspace.find((entry) => entry.id === 'workspace-local');
  expect(workspace?.prompt).toBe(`work in ${workspace?.root}`);
  expect(file.runner[0]?.runner_prompt).toMatch(/^origins are at \/.*\/origins$/);
  expect(fs.existsSync(workspace?.root as string)).toBe(true);
});
