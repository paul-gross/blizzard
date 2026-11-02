import { openLookup, type runnerApi, type Tone } from 'fleet';

import type { SelftestRunRead } from './selftest.query';

/** One harness's selftest row — its health, its latest run as last read, and whether a Run is
 * in flight for it (starting, or its run still `running`). */
export interface SelftestRow {
  readonly harness: string;
  readonly available: boolean;
  readonly run: runnerApi.SelfTestView | null;
  readonly busy: boolean;
  readonly error: string | null;
}

/**
 * One row per `harness_health` item, in its order. A harness's run is its latest read, else the
 * run its start answered; `busy` holds while its start is pending or its run is `running`. The
 * error is the start's failure, else the run read's.
 */
export function selftestRows(
  health: readonly runnerApi.HarnessHealthView[],
  started: ReadonlyMap<string, runnerApi.SelfTestView>,
  reads: ReadonlyMap<string, SelftestRunRead>,
  starting: string | null,
  startErrors: ReadonlyMap<string, string>,
): readonly SelftestRow[] {
  return health.map((item) => {
    const read = reads.get(item.harness_id);
    const run = read?.run ?? started.get(item.harness_id) ?? null;
    return {
      harness: item.harness_id,
      available: item.available,
      run,
      busy: starting === item.harness_id || run?.status === 'running',
      error: startErrors.get(item.harness_id) ?? read?.error ?? null,
    };
  });
}

/** A run's status as a badge tone — an unlisted status reads as idle. */
export const selftestTone = openLookup<'running' | 'passed' | 'failed', Tone>(
  { running: 'spawning', passed: 'done', failed: 'needs' },
  () => 'idle',
);

/** The harness a start is in flight for, or `null` while none is. */
export function startingHarness(pending: boolean, harness: string | undefined): string | null {
  return pending ? (harness ?? null) : null;
}
