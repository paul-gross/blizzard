import * as crypto from 'node:crypto';
import { test as playwrightTest, type TestInfo } from '@playwright/test';
import { LATENCY_ATTACHMENT, recordsOf } from './client/latency';
import { Ledger } from './client/ledger';
import { type OtlpSink, startOtlpSink } from './otlp-sink';

/** A name from the test's title and a random suffix, so two runs of one test never collide. */
export function uniqueName(testInfo: TestInfo, prefix = ''): string {
  const slug = testInfo.title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 24)
    .replace(/-$/, '');
  return `${prefix}${slug}-${crypto.randomBytes(3).toString('hex')}`;
}

/**
 * What a worker holds for a describe block that shares one stack across its tests (`test.use({ held: 'name' })`): the
 * stacks it built, each torn down when the worker ends, and the values the block's tests memoized beside them.
 */
export interface HeldStacks {
  /** The held value for `key`, built by the first test to ask and handed to every later one. */
  acquire<T>(key: string, build: () => Promise<{ value: T; dispose: () => Promise<void> }>): Promise<T>;
  /** The result of `produce` for `key`, produced once on the held stack and shared by every test of the block. */
  memo<T>(key: string, produce: () => Promise<T>): Promise<T>;
}

/** The registry a worker holds its stacks in: built on first ask, memoized beside them, torn down last built first. */
export function createHeldStacks(): HeldStacks & { disposeAll(): Promise<void> } {
  const built = new Map<string, Promise<{ value: unknown; dispose: () => Promise<void> }>>();
  const memos = new Map<string, Promise<unknown>>();
  return {
    acquire: async <T>(key: string, build: () => Promise<{ value: T; dispose: () => Promise<void> }>): Promise<T> => {
      let found = built.get(key);
      if (found === undefined) {
        found = build();
        built.set(key, found);
      }
      return (await found).value as T;
    },
    memo: <T>(key: string, produce: () => Promise<T>): Promise<T> => {
      let found = memos.get(key);
      if (found === undefined) {
        found = produce();
        memos.set(key, found);
      }
      return found as Promise<T>;
    },
    // A host before the hub it enrolled at; a stack whose build failed has nothing to dispose.
    disposeAll: async () => {
      const failures: unknown[] = [];
      for (const stack of [...built.values()].reverse()) {
        const settled = await stack.then((value) => value, () => undefined);
        await settled?.dispose().catch((error: unknown) => failures.push(error));
      }
      built.clear();
      memos.clear();
      if (failures.length > 0) throw failures[0];
    },
  };
}

interface WorkerFixtures {
  heldStacks: HeldStacks;
}

interface BaseFixtures {
  /**
   * The name of the stack a describe block holds across its tests, or `undefined` for a test that gets a stack of its
   * own. Set it with `test.use` on one describe block alone, and run that block's tests in order on one worker.
   */
  held: string | undefined;
  /**
   * Produces a value once for the held stack: the first test of the block to ask runs `produce`, every later one gets
   * its result, or its failure. Refused in a test that holds no stack.
   */
  once: <T>(name: string, produce: () => Promise<T>) => Promise<T>;
  ledger: Ledger;
  attachLatency: void;
  /** Starts a loopback OTLP/HTTP receiver, closed when the test ends. */
  otlpSink: () => Promise<OtlpSink>;
}

/**
 * `test` with every fixture that reaches no hub: the hub world (`tenancy.ts`) and the runner world (`mock-world.ts`)
 * both extend it, and it imports neither.
 */
export const base = playwrightTest.extend<BaseFixtures, WorkerFixtures>({
  heldStacks: [
    async ({}, use) => {
      const held = createHeldStacks();
      await use(held);
      await held.disposeAll();
    },
    { scope: 'worker' },
  ],

  held: [undefined, { option: true }],

  once: async ({ held, heldStacks }, use) => {
    await use((name, produce) => {
      if (held === undefined) throw new Error('once() memoizes for a held stack, and this test holds none. Name one with test.use({ held }).');
      return heldStacks.memo(`${held}:${name}`, produce);
    });
  },

  ledger: async ({}, use) => {
    await use(new Ledger());
  },

  // Runs for every test: the calls its clients made, each with its duration, attached for the reporter's summary.
  attachLatency: [
    async ({}, use, testInfo) => {
      await use();
      const calls = recordsOf(testInfo);
      if (calls.length > 0) await testInfo.attach(LATENCY_ATTACHMENT, { body: JSON.stringify(calls), contentType: 'application/json' });
    },
    { auto: true },
  ],

  otlpSink: async ({}, use) => {
    const started: OtlpSink[] = [];
    await use(async () => {
      const sink = await startOtlpSink();
      started.push(sink);
      return sink;
    });
    await Promise.all(started.map((sink) => sink.close()));
  },
});
