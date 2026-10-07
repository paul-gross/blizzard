import { ChangeDetectionStrategy, Component, computed, effect, inject } from '@angular/core';
import { Router, RouterOutlet } from '@angular/router';
import { acceptGardenProposalMutationKey, passGardenProposalMutationKey } from '../../core/mutation-keys';
import { asyncState, injectPendingMutationVariables, KitMasterDetail, KitChips, KitSelect, type GardenProposalView, type KitAsyncStateValue, type KitChipOption, ViewportService } from 'fleet';
import { FleetProposalList, type ProposalListRowVm } from './proposal-list';
import { type GardenProposalAcceptVars, type GardenProposalPassVars } from './garden-proposal.mutations';
import { injectHubGardenProposalsQuery } from './garden-proposals.query';
import {
  ALL_CLASSES,
  ALL_ROUTINES,
  CLASS_VALUE_PREFIX,
  SHOW_ALL,
  classChipValue,
  filterProposals,
  pendingClosureIds,
  proposalClassChips,
  proposalListRows,
  proposalRoutineChips,
  waitingChipValue,
} from './gardening-proposals-page.model';

import { injectChildRouteParam, injectQueryFilters } from '../../core/route-state';

/**
 * The `/gardening/proposals` sub-tab
 * (`blizzard-product:/delivered/garden/user-interface.md` §The docket) — the proposal
 * docket, filtered client-side by waiting state, by class, and by routine (`GET
 * /api/garden-proposals` declares no query parameters), beside a
 * `<router-outlet>` holding whichever proposal the URL names
 * (`gardening-proposal-detail.ts`).
 *
 * `gardening-scopes-page.ts`'s own parent-list/child-detail shape. All three
 * filters live in the query string (`route-state.ts`), so a pick survives a row
 * click and a filtered docket is a link the operator can hand somebody.
 *
 * A container: it injects the one list read and derives the rows the
 * presentational {@link FleetProposalList} renders. The class and routine chips
 * both come from the fetched data (`class` is the deployment's own
 * opaque vocabulary, never a hardcoded list; `routine_name` likewise, though it is
 * blizzard's own vocabulary rather than the deployment's).
 *
 * On desktop, this is the one tab whose bare route does not rest on an empty pane:
 * {@link reconcileSelection} sends it to the first row of the *filtered* set. On
 * mobile the bare route is deliberately the docket screen, and a row pick drills
 * into its detail. Either way, the URL names exactly what the page is showing.
 */
@Component({
  selector: 'app-gardening-proposals-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetProposalList, KitMasterDetail, KitChips, KitSelect, RouterOutlet],
  templateUrl: './gardening-proposals-page.html',
  styleUrl: './gardening-proposals-page.css',
  host: {
    '[class.mobile]': 'mobile()',
    '[class.detail-open]': 'proposalId() !== null',
  },
})
export class GardeningProposalsPage {
  private readonly router = inject(Router);
  private readonly viewport = inject(ViewportService);
  private readonly url = injectQueryFilters();
  private readonly proposalsQuery = injectHubGardenProposalsQuery();

  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');

  private readonly proposals = computed<readonly GardenProposalView[]>(() => this.proposalsQuery.data() ?? []);

  /** The `proposalId` the active detail child names (`route-state.ts`). */
  protected readonly proposalId = injectChildRouteParam('proposalId');

  private readonly waitingOnly = computed<boolean>(() => this.url.read('show') !== SHOW_ALL);

  /** Every proposal id a Pass or Accept mutation is currently pending for
   * (`bzh:frontend-pending-override`) — read by `mutationKey` alone; this container
   * owns neither mutation. Both are the domain's own two closing verbs
   * (`domain/findings-and-proposals.md` "Closing a proposal: pass or accept") and
   * closure is terminal — {@link isGardenProposalWaiting} reads `false` the instant
   * either lands, whichever closure kind it records, so this needs only the pending
   * id, never which of the two is in flight or what it mints. */
  private readonly pendingProposalPasses = injectPendingMutationVariables<GardenProposalPassVars>(
    passGardenProposalMutationKey,
  );
  private readonly pendingProposalAccepts = injectPendingMutationVariables<GardenProposalAcceptVars>(
    acceptGardenProposalMutationKey,
  );
  private readonly pendingProposalClosures = computed<ReadonlySet<string>>(() =>
    pendingClosureIds(this.pendingProposalPasses(), this.pendingProposalAccepts()),
  );

  /** `null` means every class — the docket's own "All classes" chip drops the param
   * rather than naming a magic class string, so a real class can never collide. */
  private readonly classFilter = computed<string | null>(() => this.url.read('class'));

  /** Every class present in the fetched data, alphabetized, each with an "All
   * classes" chip ahead of them — never a hardcoded vocabulary. */
  protected readonly classChips = computed<readonly KitChipOption[]>(() => proposalClassChips(this.proposals()));

  protected readonly classChipValue = computed<string>(() => classChipValue(this.classFilter()));

  protected onClassChoose(value: string): void {
    this.url.patch({ class: value === ALL_CLASSES ? null : value.slice(CLASS_VALUE_PREFIX.length) });
  }

  /** `All` renders first, matching the class row's own "All classes" lead, while
   * `Waiting` stays the resting selection — {@link waitingOnly} reads an absent
   * `show` param as waiting-only, so order here is presentation, not default. */
  protected readonly waitingChips: readonly KitChipOption[] = [
    { value: SHOW_ALL, label: 'All', testid: 'gardening-proposal-filter-all' },
    { value: 'waiting', label: 'Waiting', testid: 'gardening-proposal-filter-waiting' },
  ];

  /** `null` means every routine — mirrors {@link classFilter}'s "all drops the
   * param" shape. */
  private readonly routineFilter = computed<string | null>(() => this.url.read('routine'));

  /** Every routine present in the fetched data, alphabetized, each with an "All
   * routines" chip ahead of them — never a hardcoded vocabulary, mirroring
   * {@link classChips}. A routine-less operator-authored proposal
   * contributes no chip of its own: `null` names no routine to filter by. */
  protected readonly routineChips = computed<readonly KitChipOption[]>(() => proposalRoutineChips(this.proposals()));

  protected readonly routineChipValue = computed<string>(() => this.routineFilter() ?? ALL_ROUTINES);

  protected onRoutineChoose(value: string): void {
    this.url.patch({ routine: value === ALL_ROUTINES ? null : value });
  }

  protected readonly waitingChipValue = computed<string>(() => waitingChipValue(this.waitingOnly()));

  protected onWaitingChoose(value: string): void {
    this.url.patch({ show: value === SHOW_ALL ? SHOW_ALL : null });
  }

  /** The filtered set every other view model derives from; a proposal with a
   * pending Pass/Accept drops out of the waiting set (`bzh:frontend-pending-override`). */
  private readonly filteredProposals = computed<readonly GardenProposalView[]>(() =>
    filterProposals(this.proposals(), {
      waitingOnly: this.waitingOnly(),
      cls: this.classFilter(),
      routine: this.routineFilter(),
      closing: this.pendingProposalClosures(),
    }),
  );

  protected readonly listRows = computed<readonly ProposalListRowVm[]>(() =>
    proposalListRows(this.filteredProposals()),
  );

  protected readonly listState = computed<KitAsyncStateValue>(() =>
    asyncState(this.proposalsQuery, this.listRows().length === 0),
  );

  protected select(proposalId: string): void {
    void this.router.navigate(['/gardening', 'proposals', proposalId], { queryParamsHandling: 'preserve' });
  }

  protected onBack(): void {
    void this.router.navigate(['/gardening', 'proposals'], { queryParamsHandling: 'preserve' });
  }

  constructor() {
    effect(() => this.reconcileSelection());
  }

  /**
   * Keeps the URL's proposal and the docket's filters agreeing, in both
   * directions. On desktop, a selection the current filters exclude — or a bare
   * route on a docket that has rows — resolves to the first filtered row. On
   * mobile, an excluded selection returns to the filtered docket and a bare route
   * remains there until the operator picks a row.
   *
   * Gated on the list read having settled — while it is pending
   * {@link filteredProposals} reads empty, and a bare "id not in rows" check would
   * bounce a deep link straight back before its own data ever arrived. On an error
   * the selection is left alone: a failed read is not evidence the proposal was
   * filtered out.
   *
   * Converges in one step: the row it navigates to is by construction in the
   * filtered set, so the next pass returns at the guard above. `replaceUrl: true`
   * so a filter change never pushes a history entry the operator has to click back
   * through, and the filters ride along — they are why the navigation happened.
   */
  private reconcileSelection(): void {
    if (this.proposalsQuery.isPending() || this.proposalsQuery.isError()) return;
    const routed = this.proposalId();
    const rows = this.filteredProposals();
    if (routed !== null && rows.some((p) => p.proposal_id === routed)) return;
    if (this.mobile()) {
      if (routed === null) return;
      void this.router.navigate(['/gardening', 'proposals'], {
        replaceUrl: true,
        queryParamsHandling: 'preserve',
      });
      return;
    }
    const first = rows[0]?.proposal_id ?? null;
    if (routed === null && first === null) return;
    void this.router.navigate(
      first === null ? ['/gardening', 'proposals'] : ['/gardening', 'proposals', first],
      { replaceUrl: true, queryParamsHandling: 'preserve' },
    );
  }
}
