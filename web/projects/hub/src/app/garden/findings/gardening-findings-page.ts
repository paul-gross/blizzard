import { ChangeDetectionStrategy, Component, computed, effect, inject } from '@angular/core';
import { Router, RouterLink, RouterOutlet } from '@angular/router';
import { asyncState, KitBackBar, KitSelect, KitPanel, type KitAsyncStateValue, ViewportService } from 'fleet';
import { FleetFindingList, type FindingListRowVm } from '../core/finding-list';

import { injectChildRouteParam } from '../../core/route-state';
import { injectFindingsBucketFilters } from './gardening-findings-bucket-filters';
import { findingListRows } from './gardening-findings-page.model';

/**
 * The `/gardening/findings` sub-tab — the findings triage list and its filter row,
 * beside a `<router-outlet>` holding whichever finding the URL names
 * (`gardening-finding-detail.ts`, where triage itself lives).
 *
 * `gardening-scopes-page.ts`'s own parent-list/child-detail shape, and the tab
 * this shape matters most for: the filters below are what a flat pair of routes
 * would throw away on every row click.
 *
 * A container: it injects the bucket read through
 * `gardening-findings-bucket-filters.ts` and forwards plain rows to the
 * presentational {@link FleetFindingList}. The routine/scope pair, the class/state
 * filters, and the bucket read all live in that module; its resting state, with no
 * query params at all, reads every routine and every scope. All four filters render
 * as `fleet-kit-select` dropdowns, always visible, one labeled row per filter on
 * desktop — `kit-fact-list.css`'s own fixed-label-column shape — and two per row on
 * mobile, each trigger carrying its own label. Each carries a leading "All"
 * option: class and state's come from the fetched bucket's own `class` values (never
 * a hardcoded vocabulary) and the generated `FindingState` vocabulary respectively;
 * routine and scope's each name every fetched routine/scope.
 */
@Component({
  selector: 'app-gardening-findings-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetFindingList, KitBackBar, KitPanel, KitSelect, RouterLink, RouterOutlet],
  templateUrl: './gardening-findings-page.html',
  styleUrl: './gardening-findings-page.css',
  host: {
    '[class.mobile]': 'mobile()',
    '[class.detail-open]': 'findingId() !== null',
  },
})
export class GardeningFindingsPage {
  private readonly router = inject(Router);
  private readonly viewport = inject(ViewportService);

  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');

  protected readonly filters = injectFindingsBucketFilters();

  /** The `findingId` the active detail child names (`route-state.ts`). */
  protected readonly findingId = injectChildRouteParam('findingId');

  /** The bucket's rows, naming only the routine/scope dimension the active
   * filter leaves unnamed. */
  protected readonly findingListRows = computed<readonly FindingListRowVm[]>(() =>
    findingListRows(this.filters.filteredBucket(), this.filters.selectedRoutine(), this.filters.selectedScope()),
  );

  protected readonly bucketState = computed<KitAsyncStateValue>(() =>
    asyncState(this.filters.bucketQuery, this.findingListRows().length === 0),
  );

  protected selectFinding(findingId: string): void {
    void this.router.navigate(['/gardening', 'findings', findingId], { queryParamsHandling: 'preserve' });
  }

  /**
   * A route-named selection is independent of the bucket's own filters — a
   * routine/scope/class/state pick can shrink the visible rows out from under a
   * still-valid `findingId`, unlike a param naming nothing the loaded data has
   * (there a plain computed resolves to nothing selected; here the *set* itself
   * can shrink after the id was already valid, so clearing it takes a real
   * navigation to the bare list route, not a computed that just stops rendering
   * while the URL still names a finding no longer in view). This lives beside the
   * list rather than in the detail pane because it is the *list's* agreement with
   * the URL that is at stake: without it the detail, resolved by id independently
   * of the bucket, would happily keep rendering a finding the current filters
   * exclude.
   *
   * Gated on the bucket read having actually settled — `filters.bucketQuery`'s own
   * `isPending()`/`isError()` — since {@link findingListRows} reads empty while a
   * routine/scope change is still in flight, and a bare "id not in rows" check
   * would fire on every such change and clear a selection that would have survived
   * the read once it resolved. On an error the safest behavior is to leave the
   * selection alone: a failed read is not evidence the finding was filtered out.
   *
   * Derived entirely from settled query state plus the route param — never from a
   * filter-change event, which would fire before the new bucket read resolves and
   * land straight back in the pending trap above — so this can't race an
   * operator's own click or re-trigger itself: once the navigation lands,
   * {@link findingId} reads `null` and the effect no-ops. `replaceUrl: true` so a
   * filter change never pushes a history entry the operator has to click back
   * through, and the filters themselves are preserved: they are the reason the
   * navigation is happening.
   */
  constructor() {
    effect(() => {
      const id = this.findingId();
      if (id === null) return;
      if (this.filters.bucketQuery.isPending() || this.filters.bucketQuery.isError()) return;
      if (this.findingListRows().some((row) => row.findingId === id)) return;
      void this.router.navigate(['/gardening', 'findings'], {
        replaceUrl: true,
        queryParamsHandling: 'preserve',
      });
    });
  }
}
