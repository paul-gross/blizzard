import * as fs from 'node:fs';
import type { FullResult, Reporter } from '@playwright/test/reporter';
import { runDir } from './env';

/** Removes the run's directory once the run passed with `CI` unset, so a red run's logs are still on disk to read. */
export default class RunDirectoryReporter implements Reporter {
  onEnd(result: FullResult): void {
    if (result.status !== 'passed' || process.env.CI) return;
    fs.rmSync(runDir(), { recursive: true, force: true });
  }
}
