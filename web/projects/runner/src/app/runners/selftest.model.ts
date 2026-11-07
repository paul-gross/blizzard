import { openLookup, type runnerApi, type Tone } from 'fleet';

import type { SelftestRunRead, SelftestRunRef } from './selftest.query';

/** One harness's selftest row — its health, its latest run as last read, and whether a Run is
 * in flight for it (its start pending, or its run still running). */
export interface SelftestRow {
  readonly harness: string;
  readonly available: boolean;
  readonly run: runnerApi.SelfTestView | null;
  readonly busy: boolean;
  readonly error: string | null;
}

/** A run's classification: `running` still moves, `settled` no longer does, and `unclassified`
 * is a run from a runner that does not say — never treated as concluded. */
export type SelftestPhase = 'running' | 'settled' | 'unclassified';

/** A run's {@link SelftestPhase}, read from the runner's `settled` alone; its raw `status`
 * never decides it. A run without the field is `unclassified`. */
export function selftestPhase(run: { readonly settled?: boolean }): SelftestPhase {
  if (run.settled === undefined) return 'unclassified';
  return run.settled ? 'settled' : 'running';
}

/**
 * One row per `harness_health` item, in its order. A harness's run is its latest read, else the
 * run its start answered. `busy` holds while its start is pending or its run is still running,
 * unless the run's read failed with nothing read — then Run stays available. The error is the
 * start's failure, else the run read's.
 */
export function selftestRows(
  health: readonly runnerApi.HarnessHealthView[],
  started: ReadonlyMap<string, runnerApi.SelfTestView>,
  reads: ReadonlyMap<string, SelftestRunRead>,
  pending: ReadonlySet<string>,
  startErrors: ReadonlyMap<string, string>,
): readonly SelftestRow[] {
  return health.map((item) => {
    const read = reads.get(item.harness_id);
    const run = read?.run ?? started.get(item.harness_id) ?? null;
    const unreadable = read !== undefined && read.run === null && read.error !== null;
    return {
      harness: item.harness_id,
      available: item.available,
      run,
      busy: pending.has(item.harness_id) || (!unreadable && run !== null && selftestPhase(run) === 'running'),
      error: startErrors.get(item.harness_id) ?? read?.error ?? null,
    };
  });
}

/** The harnesses with a start pending, from the pending starts' variables. */
export function pendingHarnesses(pending: readonly { readonly harness: string }[]): ReadonlySet<string> {
  return new Set(pending.map((vars) => vars.harness));
}

/** Each started run as the ref its read polls by. */
export function selftestRunRefs(started: ReadonlyMap<string, runnerApi.SelfTestView>): readonly SelftestRunRef[] {
  return [...started.values()].map((run) => ({ harness: run.harness, id: run.id }));
}

/** A run's status as a badge tone — an unlisted status reads as idle. */
export const selftestTone = openLookup<runnerApi.SelfTestStatus, Tone>(
  { running: 'spawning', passed: 'done', failed: 'needs' },
  () => 'idle',
);

/** A check's outcome as a badge tone, in {@link selftestTone}'s terminal tones. */
export function checkTone(passed: boolean): Tone {
  return passed ? 'done' : 'needs';
}
