import { ChangeDetectionStrategy, Component, computed, effect, inject } from '@angular/core';
import { Router, RouterOutlet } from '@angular/router';
import {
  asyncState,
  FleetFindingList,
  KitChips,
  KitPanel,
  type FindingListRowVm,
  type KitAsyncStateValue,
} from 'fleet';

import { injectChildRouteParam } from '../route-state';
import { injectFindingsBucketFilters } from './gardening-findings-bucket-filters';

/**
 * The `/gardening/findings` sub-tab — the findings triage list and its filter row,
 * beside a `<router-outlet>` holding whichever finding the URL names
 * (`gardening-finding-detail.ts`, where triage itself lives). Split off the
 * combined runs-and-findings surface blizzard#401 Phase 3 built; runs and findings
 * are unrelated concepts and get one tab each now.
 *
 * `gardening-scopes-page.ts`'s own parent-list/child-detail shape, and the tab
 * this shape matters most for: the filters below are what a flat pair of routes
 * would throw away on every row click.
 *
 * A container: it injects the bucket read through
 * `gardening-findings-bucket-filters.ts` and forwards plain rows to the
 * presentational {@link FleetFindingList}. The routine/scope pair, the class/state
 * filters, and the bucket read all live in that module. The bucket widened to
 * every routine and every scope (blizzard#486) — its resting state, with no query
 * params at all, reads everything, no seeded pair required. All four filters
 * render as `fleet-kit-chips`, always visible (no accordion — no other gardening
 * tab collapses its filters, so this one doesn't either), one labeled row per
 * filter — `kit-fact-list.css`'s own fixed-label-column shape, so the four groups
 * read distinctly instead of running together in one row. Every chip row now
 * carries an "All" option: class and state's come from the fetched bucket's own
 * `class` values (never a hardcoded vocabulary) and the fixed seven-value state
 * vocabulary respectively; routine and scope's each name every fetched
 * routine/scope, per the widened bucket.
 */
@Component({
  selector: 'app-gardening-findings-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetFindingList, KitChips, KitPanel, RouterOutlet],
  templateUrl: './gardening-findings-page.html',
  styleUrl: './gardening-findings-page.css',
})
export class GardeningFindingsPage {
  private readonly router = inject(Router);

  protected readonly filters = injectFindingsBucketFilters();

  /** The `findingId` the active detail child names (`route-state.ts`). */
  protected readonly findingId = injectChildRouteParam('findingId');

  /** Pared to what the 320px master column renders — `observed_count` and
   * `introduced` show in the detail pane once the row is picked, not here;
   * `last_seen_at` rides both, since the row's own fourth line is the most recent
   * observation. */
  /** Only the dimension the active filter leaves unnamed shows on the row (D4): a
   * bucket widened to every routine or every scope needs each row to say which it
   * came from, but a bucket already filtered to one doesn't need it repeated on
   * every row. */
  protected readonly findingListRows = computed<readonly FindingListRowVm[]>(() =>
    this.filters.filteredBucket().map((f) => ({
      findingId: f.finding_id,
      findingClass: f.class,
      locus: f.locus,
      summary: f.summary,
      state: f.state,
      lastSeenAt: f.last_seen_at,
      routineName: this.filters.selectedRoutine() === null ? f.routine_name : null,
      scopeSlug: this.filters.selectedScope() === null ? f.scope_slug : null,
    })),
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
