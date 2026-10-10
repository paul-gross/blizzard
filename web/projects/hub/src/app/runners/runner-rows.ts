import { computed } from '@angular/core';
import { injectQuery } from '@tanstack/angular-query-experimental';

import { type RunnerRegistryView, type KitAsyncStateValue, injectNowSignal, asyncState, LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet';
import { injectProjectFilter } from 'fleet/shell';
import { boardChunksQueryOptions } from '../core/chunks.query';
import { foldRunnerRows, lensRunners, type RunnerRow } from './runner-rows.model';
import { injectHubRunnersQuery } from '../core/runners.query';

export * from './runner-rows.model';

/**
 * Folds the runners + chunks reads into {@link RunnerRow}s: each runner's claims,
 * slot-bar numerator, pace bars and served projects, derived once per registry query
 * (`bzh:frontend-formatters`).
 *
 * Under a project lens the rows are the runners serving the lensed project and the unassigned ones
 * ({@link lensRunners}). A runner is the tenant's, not a project's, so its claims and slot bar are folded from the whole
 * tenant's board list on every lens: a runner busy on another project's chunk still reads as busy.
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
  const chunksQuery = injectQuery(() => ({
    ...boardChunksQueryOptions(null),
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
  const projectFilter = injectProjectFilter();

  const runners = computed<readonly RunnerRegistryView[]>(() => lensRunners(runnersQuery.data() ?? [], projectFilter()));

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
