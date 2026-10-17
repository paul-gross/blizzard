import { ChangeDetectionStrategy, Component, computed, effect, inject } from '@angular/core';
import { Router, RouterOutlet } from '@angular/router';
import { asyncState, KitButton, KitSelect, KitMasterDetail, KitPanel, type KitAsyncStateValue, ViewportService } from 'fleet';
import { FleetFindingList, type FindingListRowVm } from '../core/finding-list';

import { injectChildRouteParam } from '../../core/route-state';
import { injectFindingsBucketFilters } from './gardening-findings-bucket-filters';
import { findingListRows } from './gardening-findings-page.model';
import { ProjectLens } from '../../core/project-lens';
import { firstRowToOpen } from '../../core/first-row';

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
  * option: class comes from the scoped distinct-classes read and state from the
  * generated vocabulary; neither depends on the loaded pages.
 * routine and scope's each name every fetched routine/scope.
 */
@Component({
  selector: 'app-gardening-findings-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetFindingList, KitButton, KitMasterDetail, KitPanel, KitSelect, RouterOutlet],
  templateUrl: './gardening-findings-page.html',
  styleUrl: './gardening-findings-page.css',
  host: {
    '[class.mobile]': 'mobile()',
    '[class.detail-open]': 'findingId() !== null',
  },
})
export class GardeningFindingsPage {
  private readonly lens = inject(ProjectLens);

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
    void this.router.navigate(this.lens.link(['/gardening', 'findings', findingId]), { queryParamsHandling: 'preserve' });
  }

  protected onBack(): void {
    void this.router.navigate(this.lens.link(['/gardening', 'findings']), { queryParamsHandling: 'preserve' });
  }

  /**
   * On desktop the bare route opens the first loaded row. A named finding stays
   * selected independently of the paginated list; the child fetches it by id.
   */
  constructor() {
    effect(() => {
      const query = this.filters.bucketQuery;
      const settled = !query.isPending() && !query.isError();
      const ids = this.findingListRows().map((row) => row.findingId);
      const first = firstRowToOpen(this.findingId(), ids, settled, this.mobile());
      if (first === null) return;
      void this.router.navigate(this.lens.link(['/gardening', 'findings', first]), {
        replaceUrl: true,
        queryParamsHandling: 'preserve',
      });
    });
  }

  protected older(): void {
    if (this.filters.bucketQuery.hasNextPage() && !this.filters.bucketQuery.isFetchingNextPage()) {
      void this.filters.bucketQuery.fetchNextPage();
    }
  }
}
