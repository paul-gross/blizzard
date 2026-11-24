import type { Tone } from 'fleet';

import type { HarnessHealth } from '../core/runner-status.domain';
import type { KnownSelftestStatus, SelftestRun, SelftestStatus } from './selftest.domain';

/** One harness's latest selftest run, by id. */
export interface SelftestRunRef {
  readonly harness: string;
  readonly id: string;
}

/** A harness's latest run as last read, or the read's failure. */
export interface SelftestRunRead {
  readonly run: SelftestRun | null;
  readonly error: string | null;
}

/** One harness's selftest row — its health, its latest run as last read, and whether a Run is
 * in flight for it (its start pending, or its run still running). */
export interface SelftestRow {
  readonly harness: string;
  readonly available: boolean;
  readonly run: SelftestRun | null;
  readonly busy: boolean;
  readonly error: string | null;
}

/** A run's classification: `running` still moves, `settled` no longer does, and `unclassified`
 * is a run from a runner that does not say — never treated as concluded. */
export type SelftestPhase = 'running' | 'settled' | 'unclassified';

/** A run's {@link SelftestPhase}, read from the runner's `settled` alone; its raw `status`
 * never decides it. A run without the field is `unclassified`. */
export function selftestPhase(run: { readonly settled: boolean | null }): SelftestPhase {
  if (run.settled === null) return 'unclassified';
  return run.settled ? 'settled' : 'running';
}

/**
 * One row per `harness_health` item, in its order. A harness's run is its latest read, else the
 * run its start answered. `busy` holds while its start is pending or its run is still running,
 * unless the run's read failed with nothing read — then Run stays available. The error is the
 * start's failure, else the run read's.
 */
export function selftestRows(
  health: readonly HarnessHealth[],
  started: ReadonlyMap<string, SelftestRun>,
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
export function selftestRunRefs(started: ReadonlyMap<string, SelftestRun>): readonly SelftestRunRef[] {
  return [...started.values()].map((run) => ({ harness: run.harness, id: run.id }));
}

const SELFTEST_TONES: Readonly<Record<KnownSelftestStatus, Tone>> = { running: 'spawning', passed: 'done', failed: 'needs' };

/** A run's status as a badge tone — an unknown status reads as idle. */
export function selftestTone(status: SelftestStatus): Tone {
  return typeof status === 'string' ? SELFTEST_TONES[status] : 'idle';
}

/** A check's outcome as a badge tone, in {@link selftestTone}'s terminal tones. */
export function checkTone(passed: boolean): Tone {
  return passed ? 'done' : 'needs';
}
