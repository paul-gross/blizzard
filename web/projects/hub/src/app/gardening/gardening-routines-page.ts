import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { Router, RouterOutlet } from '@angular/router';
import {
  asyncState,
  FleetRoutineList,
  injectHubGraphsQuery,
  injectHubRoutinesQuery,
  KitPanel,
  type GraphSummaryView,
  type KitAsyncStateValue,
  type RoutineListRowVm,
  type RoutineView,
} from 'fleet';

import { injectChildRouteParam } from '../route-state';
import { isRoutineBlocked } from './gardening-effective-graph';

/**
 * The `/gardening/routines` sub-tab (`plans/garden/user-interface.md` §Declaring
 * and running a routine) — the routine list, beside a `<router-outlet>` holding
 * whichever routine the URL names (`gardening-routine-detail.ts`). Split off this
 * page's own combined routines-and-scopes surface (blizzard#399/#397); routines
 * and scopes are unrelated concepts that only used to share a tab.
 *
 * `gardening-scopes-page.ts`'s own parent-list/child-detail shape, for the same
 * reason: nesting the detail under the list is what keeps this component — and
 * the list's scroll position — alive across a row pick.
 *
 * A container: it injects the routine and graph queries and forwards plain view
 * models to the presentational {@link FleetRoutineList}, which injects no query of
 * its own. `blocked` (D7) is resolved per row off the same effective-graph lookup
 * a run itself refuses on (`gardening-effective-graph.ts`), shared with the detail
 * pane that resolves it for the selected routine.
 */
@Component({
  selector: 'app-gardening-routines-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetRoutineList, KitPanel, RouterOutlet],
  templateUrl: './gardening-routines-page.html',
  styleUrl: './gardening-routines-page.css',
})
export class GardeningRoutinesPage {
  private readonly router = inject(Router);

  private readonly routinesQuery = injectHubRoutinesQuery();
  private readonly graphsQuery = injectHubGraphsQuery();

  private readonly routines = computed<readonly RoutineView[]>(() => this.routinesQuery.data() ?? []);
  private readonly graphs = computed<readonly GraphSummaryView[]>(() => this.graphsQuery.data() ?? []);

  /** The `routineName` the active detail child names (`route-state.ts`). */
  private readonly routineNameParam = injectChildRouteParam('routineName');

  /** The effective selection: the route param if it still names a routine the
   * loaded data actually has, else `null` — never a stale highlight left over from
   * a routine that no longer exists. */
  protected readonly selectedRoutineName = computed<string | null>(() => {
    const routineName = this.routineNameParam();
    if (routineName === null) return null;
    return this.routines().some((r) => r.name === routineName) ? routineName : null;
  });

  protected selectRoutine(name: string): void {
    void this.router.navigate(['/gardening', 'routines', name], { queryParamsHandling: 'preserve' });
  }

  protected readonly listRows = computed<readonly RoutineListRowVm[]>(() =>
    this.routines().map((r) => ({
      routineId: r.routine_id,
      name: r.name,
      graphName: r.graph_name,
      blocked: isRoutineBlocked(this.graphs(), this.graphsQuery.isPending(), r.graph_name),
    })),
  );

  protected readonly listState = computed<KitAsyncStateValue>(() =>
    asyncState(this.routinesQuery, this.routines().length === 0),
  );
}
