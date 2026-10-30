import * as path from 'node:path';
import * as crypto from 'node:crypto';
import { test, expect } from '@playwright/test';
import { startCollector } from '../../collector';

test('a missing or unusable collector fails rather than skipping', async ({}, testInfo) => {
  await expect(startCollector(testInfo.outputPath('missing'), 1, path.join(testInfo.outputDir, 'missing-otelcol'))).rejects.toThrow('otelcol-contrib');
  await expect(startCollector(testInfo.outputPath('unusable'), 1, process.execPath)).rejects.toThrow('otelcol-contrib');
});

test('a trace-specific probe observes a dropped decision without a forwarded span', async ({}, testInfo) => {
  const collector = await startCollector(testInfo.outputPath('decision'), 1);
  try {
    const traceId = crypto.randomBytes(16).toString('hex');
    await collector.sendProbe(traceId);
    await expect.poll(() => collector.decision(traceId), { timeout: 30000 }).toBe('dropped');
    expect(collector.forwarded.spans().filter((span) => span.traceId === traceId)).toEqual([]);
  } finally {
    await collector.close();
  }
});
