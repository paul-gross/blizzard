import { computed, Injectable, type Signal } from '@angular/core';
import {
  asyncState,
  defaultRoutineWindow,
  injectHubRunsQuery,
  type KitAsyncStateValue,
  type RunListCountsVm,
  type RunListRowVm,
} from 'fleet';

/** A run's counts triple, summed across every set it delivered — `null` when it
 * delivered none, so the row renders no triple rather than a misleading `+0`. */
function summedCounts(
  delivered: readonly { added_count: number; observed_count: number; gone_count: number }[],
): RunListCountsVm | null {
  if (delivered.length === 0) return null;
  return delivered.reduce(
    (sum, set) => ({
      added: sum.added + set.added_count,
      observed: sum.observed + set.observed_count,
      gone: sum.gone + set.gone_count,
    }),
    { added: 0, observed: 0, gone: 0 },
  );
}

/**
 * The `/gardening/runs` tab's one run-list read, shared by the list route and the
 * delta pane nested under it. Provided on `GardeningRunsPage`, so both halves of
 * the tab resolve the same instance and it is torn down when the tab is left.
 *
 * The other four gardening tabs need no state object like this: their two halves
 * each inject the cache-keyed queries they need and land on the same cached data.
 * This read cannot, because its key carries a window cut from the wall clock — two
 * independent constructions would key on two different instants and fetch the same
 * endpoint twice, to subtly different answers. The window is therefore computed
 * once, here, and a page reload is what refreshes it.
 */
@Injectable()
export class GardeningRunsState {
  /** The list's fixed reporting window. Shares the routine trend/sweeps vocabulary
   * rather than the read's own 24-hour server default. */
  private readonly window = defaultRoutineWindow(Date.now());

  readonly runsQuery = injectHubRunsQuery(() => this.window.since);

  readonly listRows: Signal<readonly RunListRowVm[]> = computed(() =>
    (this.runsQuery.data() ?? []).map((row) => ({
      chunkId: row.chunk_id,
      routineName: row.routine_name,
      scopeSlug: row.scope_slug,
      mode: row.mode,
      mintedAt: row.minted_at,
      outcome: row.outcome,
      escalated: row.escalation !== null,
      counts: summedCounts(row.delivered),
    })),
  );

  readonly listState: Signal<KitAsyncStateValue> = computed(() =>
    asyncState(this.runsQuery, this.listRows().length === 0),
  );

  /** When `chunkId` was minted, off the matching list row — `null` when the run has
   * aged out of the window above, which the delta read carries no instant to cover. */
  mintedAtFor(chunkId: string): string | null {
    return this.listRows().find((row) => row.chunkId === chunkId)?.mintedAt ?? null;
  }
}
