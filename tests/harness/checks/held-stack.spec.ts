import { test as plain } from '@playwright/test';
import { createHeldStacks } from '../base';
import { expect, test } from '../fixtures';

plain.describe('the registry a worker holds its stacks in', () => {
  plain('builds a stack once for every ask, and memoizes a produced value beside it', async () => {
    const held = createHeldStacks();
    let built = 0;
    let produced = 0;
    const build = async () => ({ value: { id: ++built }, dispose: async () => undefined });
    const [first, second] = await Promise.all([held.acquire('hub', build), held.acquire('hub', build)]);
    expect(first).toBe(second);
    expect(built).toBe(1);
    const values = await Promise.all([held.memo('night', async () => ++produced), held.memo('night', async () => ++produced)]);
    expect(values).toEqual([1, 1]);
    await held.disposeAll();
  });

  plain('stops what it holds when the worker ends, the last built first, and tolerates a build that failed', async () => {
    const held = createHeldStacks();
    const order: string[] = [];
    const stack = (name: string) => async () => ({ value: name, dispose: async () => void order.push(name) });
    await held.acquire('hub', stack('hub'));
    await held.acquire('host', stack('host'));
    await expect(held.acquire('broken', async () => { throw new Error('no stack'); })).rejects.toThrow('no stack');
    await held.disposeAll();
    expect(order).toEqual(['host', 'hub']);
  });
});

// One fresh hub and one runner host held by the block: both tests see the same processes, and a value produced for the
// block once is the value every test of it reads. The block's tests run in order on one worker.
test.describe('a block that holds its stack', () => {
  test.describe.configure({ mode: 'default' });
  test.use({ isolation: 'fresh-hub', held: 'check-held' });

  test('the first test builds the stack and produces the block’s value', async ({ freshHub, runnerHost, once }, testInfo) => {
    expect(await once('pid', async () => freshHub.pid)).toBe(freshHub.pid);
    expect(await once('host', async () => runnerHost.pid)).toBe(runnerHost.pid);
    expect(await once('first', async () => testInfo.title)).toBe(testInfo.title);
  });

  test('the second test finds the same hub, the same host and the first test’s value', async ({ freshHub, runnerHost, once }) => {
    expect(await once('pid', async () => -1)).toBe(freshHub.pid);
    expect(await once('host', async () => -1)).toBe(runnerHost.pid);
    expect(await once('first', async () => 'the second test produced it')).toBe('the first test builds the stack and produces the block’s value');
  });
});

test.describe('a test outside the block', () => {
  test.use({ isolation: 'fresh-hub' });

  test('holds no stack, so once() refuses it and its hub is its own', async ({ once, freshHub }) => {
    expect(() => once('night', async () => 1)).toThrow(/holds none/);
    expect(freshHub.pid).toBeGreaterThan(0);
  });
});

test.describe('a held stack below fresh-hub', () => {
  test.use({ isolation: 'fresh-tenant', held: 'check-held-tenant' });
  test.fail(process.env.BLIZZARD_ISOLATION_RAISE !== '1', 'held names fresh-hub');
  test('fails before the test starts', ({ testHub }) => {
    void testHub;
  });
});
