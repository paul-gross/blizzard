import { computed } from '@angular/core';
import { injectQuery } from '@tanstack/angular-query-experimental';

import { type KitAsyncStateValue, injectNowSignal, asyncState, LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet';
import { injectProjectFilter } from 'fleet/shell';
import { boardChunksQueryOptions } from '../core/chunks.query';
import { foldRunnerRows, lensRunners, type RunnerRow } from './runner-rows.model';
import { foldHostCards, type HostCard } from './host-rows.model';
import { injectHubHostsQuery } from './hosts.query';
import { injectHubRunnersQuery } from '../core/runners.query';

/**
 * Folds the runners, hosts and chunks reads into {@link RunnerRow}s — each runner's claims,
 * slot-bar numerator, pace bars, served projects and bindings — and into the {@link HostCard}s
 * the rows group under, derived once per registry query (`bzh:frontend-formatters`).
 *
 * Under a project lens the rows are the runners serving the lensed project and the unassigned ones
 * ({@link lensRunners}). A runner is the tenant's, not a project's, so its claims and slot bar are folded from the whole
 * tenant's board list on every lens: a runner busy on another project's chunk still reads as busy.
 *
 * Under a lens a card holding no lensed runner is hidden ({@link foldHostCards}).
 *
 * Retired runners and hosts are folded in only while `includeRetired()` is true. Returns only
 * the shared reads and their fold; permission reads and pause mutations are left to
 * the caller.
 */
export function injectRunnerRows(includeRetired: () => boolean = () => false): {
  readonly rows: () => readonly RunnerRow[];
  readonly cards: () => readonly HostCard[];
  readonly state: () => KitAsyncStateValue;
} {
  const runnersQuery = injectHubRunnersQuery(includeRetired);
  const hostsQuery = injectHubHostsQuery(includeRetired);
  const chunksQuery = injectQuery(() => ({
    ...boardChunksQueryOptions(null),
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
  const projectFilter = injectProjectFilter();


  /** The shared one-second clock — this data refreshes on `runner-changed` SSE,
   * not by polling, so the job here is keeping each row's "seen Ns ago" and each pace
   * bar's *elapsed* fraction visually live between those pushes, not driving fresh reads. */
  const now = injectNowSignal();

  /** Every runner, unlensed, with its claims, slot-bar numerator, subscription pace groups and
   * bindings folded on; recomputes on {@link now} so elapsed bars keep advancing between
   * `runner-changed` pushes. */
  const allRows = computed<readonly RunnerRow[]>(() =>
    foldRunnerRows(runnersQuery.data() ?? [], chunksQuery.data() ?? [], now(), hostsQuery.data() ?? []),
  );

  const rows = computed<readonly RunnerRow[]>(() => lensRunners(allRows(), projectFilter()));

  const cards = computed<readonly HostCard[]>(() =>
    foldHostCards(
      hostsQuery.data() ?? [],
      allRows(),
      projectFilter() === null ? null : rows(),
      chunksQuery.data() ?? [],
      now(),
    ),
  );

  const state = computed<KitAsyncStateValue>(() =>
    asyncState(runnersQuery, rows().length === 0 && cards().length === 0),
  );

  return { rows, cards, state };
}
