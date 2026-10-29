import * as path from 'node:path';
import { test, expect } from '@playwright/test';
import { startCollector } from '../../collector';

test('a missing or unusable collector fails rather than skipping', async ({}, testInfo) => {
  await expect(startCollector(testInfo.outputPath('missing'), 1, path.join(testInfo.outputDir, 'missing-otelcol'))).rejects.toThrow('otelcol-contrib');
  await expect(startCollector(testInfo.outputPath('unusable'), 1, process.execPath)).rejects.toThrow('otelcol-contrib');
});
