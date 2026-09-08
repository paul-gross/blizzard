import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { Router, RouterLink, RouterOutlet } from '@angular/router';
import { FleetRunList, KitBackBar, KitPanel, ViewportService } from 'fleet';

import { injectChildRouteParam } from '../route-state';
import { GardeningRunsState } from './gardening-runs-state';

/**
 * The `/gardening/runs` sub-tab — the run list, beside a `<router-outlet>` holding
 * whichever run's delta the URL names (`gardening-run-detail.ts`). The run list
 * and a run's own delta are unrelated concepts and get one tab each.
 *
 * `gardening-scopes-page.ts`'s own parent-list/child-detail shape, for the same
 * reason: nesting the detail under the list is what keeps this component — and the
 * list's scroll position — alive across a row pick. This tab also provides the
 * {@link GardeningRunsState} the two halves share, since the list read's own
 * reporting window cannot be re-derived independently on the other side of the
 * route boundary.
 *
 * A container: the run rows come off that shared read and go to the presentational
 * {@link FleetRunList}, which injects no query of its own.
 */
@Component({
  selector: 'app-gardening-runs-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetRunList, KitBackBar, KitPanel, RouterLink, RouterOutlet],
  templateUrl: './gardening-runs-page.html',
  styleUrl: './gardening-runs-page.css',
  providers: [GardeningRunsState],
  host: {
    '[class.mobile]': 'mobile()',
    '[class.detail-open]': 'chunkId() !== null',
  },
})
export class GardeningRunsPage {
  private readonly router = inject(Router);
  private readonly viewport = inject(ViewportService);

  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');

  protected readonly runs = inject(GardeningRunsState);

  /** The `chunkId` the active detail child names (`route-state.ts`). */
  protected readonly chunkId = injectChildRouteParam('chunkId');

  protected selectRun(chunkId: string): void {
    void this.router.navigate(['/gardening', 'runs', chunkId], { queryParamsHandling: 'preserve' });
  }
}
