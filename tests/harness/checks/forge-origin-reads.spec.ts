import { uniqueName } from '../base';
import { expect, test } from '../fixtures';

// The forge's bare repositories are read by `owner/name`, the way the forge keeps them.

test.use({ isolation: 'fresh-tenant' });

test('the forge reads a created repository’s seeded tree, a path’s history and a file', async ({ forge }, testInfo) => {
  const name = uniqueName(testInfo, 'origin-reads');
  const created = await forge.createRepository('blizzard', name);
  const repo = created.full_name;

  const tree = await forge.originTree(repo, created.default_branch);
  expect(tree?.length, 'the seeded tree is empty').toBeGreaterThan(0);
  const seeded = tree![0]!;

  const history = await forge.originLog(repo, created.default_branch);
  expect(history?.map((commit) => commit.subject)).toEqual(['chore: seed repository']);
  expect((await forge.originLog(repo, created.default_branch, seeded))?.map((commit) => commit.subject)).toEqual(['chore: seed repository']);
  expect(await forge.originLog(repo, created.default_branch, 'no-such-file.md')).toEqual([]);

  expect(await forge.originFile(repo, created.default_branch, seeded)).toEqual(expect.any(String));
  expect(await forge.originFile(repo, history![0]!.sha, seeded), 'a commit sha reads as a ref').toEqual(expect.any(String));
});

test('a forge repository, ref or path that is not there reads as undefined', async ({ forge }, testInfo) => {
  const created = await forge.createRepository('blizzard', uniqueName(testInfo, 'origin-reads'));
  expect(await forge.originTree('blizzard/no-such-repo', 'main')).toBeUndefined();
  expect(await forge.originTree(created.full_name, 'no-such-branch')).toBeUndefined();
  expect(await forge.originLog(created.full_name, 'no-such-branch')).toBeUndefined();
  expect(await forge.originFile(created.full_name, 'main', 'no-such-file.md')).toBeUndefined();
});
