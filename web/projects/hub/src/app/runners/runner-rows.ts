import { computed } from '@angular/core';

import { type RunnerView, type KitAsyncStateValue, injectNowSignal, asyncState } from 'fleet';
import { injectHubBoardChunksQuery } from '../core/chunks.query';
import { foldRunnerRows, type RunnerRow } from './runner-rows.model';
import { injectHubRunnersQuery } from './runners.query';

export * from './runner-rows.model';

/**
 * Folds the runners + chunks reads into {@link RunnerRow}s: each runner's claims,
 * slot-bar numerator, and pace bars, derived once per registry query
 * (`bzh:frontend-formatters`).
 *
 * Retired runners are folded in only while `includeRetired()` is true. Returns only
 * the shared reads and their fold; permission reads and pause mutations are left to
 * the caller.
 */
export function injectRunnerRows(includeRetired: () => boolean = () => false): {
  readonly rows: () => readonly RunnerRow[];
  readonly state: () => KitAsyncStateValue;
} {
  const runnersQuery = injectHubRunnersQuery(includeRetired);
  const chunksQuery = injectHubBoardChunksQuery();

  const runners = computed<readonly RunnerView[]>(() => runnersQuery.data() ?? []);

  const state = computed<KitAsyncStateValue>(() => asyncState(runnersQuery, runners().length === 0));

  /** A slow-ticking clock — this data refreshes on `runner-changed` SSE,
   * not by polling, so the only job here is keeping each pace bar's *elapsed* fraction
   * visually live between those pushes, not driving fresh reads. */
  const now = injectNowSignal(30_000);

  /** Each runner with its claims, slot-bar numerator, and subscription pace groups folded on;
   * recomputes on {@link now} so elapsed bars keep advancing between `runner-changed` pushes. */
  const rows = computed<readonly RunnerRow[]>(() => foldRunnerRows(runners(), chunksQuery.data() ?? [], now()));

  return { rows, state };
}
