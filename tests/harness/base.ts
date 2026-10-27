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

interface BaseFixtures {
  ledger: Ledger;
  attachLatency: void;
  /** Starts a loopback OTLP/HTTP receiver, closed when the test ends. */
  otlpSink: () => Promise<OtlpSink>;
}

/**
 * `test` with every fixture that reaches no hub: the hub world (`tenancy.ts`) and the runner world (`mock-world.ts`)
 * both extend it, and it imports neither.
 */
export const base = playwrightTest.extend<BaseFixtures>({
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
