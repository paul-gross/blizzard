import { computed, inject, Injectable, type Signal } from '@angular/core';
import { FLEET_CLOCK, asyncState, type KitAsyncStateValue } from 'fleet';
import { defaultRoutineWindow } from '../core/routine-window';
import { injectHubRunsQuery } from './garden-runs.query';
import { mintedAtFor, runListRows } from './gardening-runs-state.model';
import { type RunListRowVm } from './run-list';

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
 * once, here, off `FLEET_CLOCK`, and a page reload is what refreshes it.
 */
@Injectable()
export class GardeningRunsState {
  /** The list's fixed reporting window. Shares the routine trend/sweeps vocabulary
   * rather than the read's own 24-hour server default. */
  private readonly window = defaultRoutineWindow(inject(FLEET_CLOCK)());

  readonly runsQuery = injectHubRunsQuery(() => this.window.since);

  readonly listRows: Signal<readonly RunListRowVm[]> = computed(() => runListRows(this.runsQuery.data() ?? []));

  readonly listState: Signal<KitAsyncStateValue> = computed(() =>
    asyncState(this.runsQuery, this.listRows().length === 0),
  );

  /** When `chunkId` was minted, off the matching list row — `null` when the run has
   * aged out of the window above, which the delta read carries no instant to cover. */
  mintedAtFor(chunkId: string): string | null {
    return mintedAtFor(this.listRows(), chunkId);
  }
}
