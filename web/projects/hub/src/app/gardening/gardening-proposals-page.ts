import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';
import {
  asyncState,
  FleetProposalList,
  injectHubGardenProposalsQuery,
  isGardenProposalWaiting,
  KitAsyncState,
  KitChips,
  type GardenProposalView,
  type KitAsyncStateValue,
  type KitChipOption,
  type ProposalListRowVm,
} from 'fleet';

/** The docket's waiting filter — a proposal not yet closed, or every proposal
 * regardless of closure. */
type WaitingFilter = 'waiting' | 'all';

const ALL_CLASSES = 'all';

/**
 * The `/gardening/proposals` sub-tab (blizzard#403,
 * `blizzard-product:/plans/garden/user-interface.md` §The docket) — the proposal
 * list, filtered client-side by waiting state and by class (Decision 1: `GET
 * /api/garden-proposals` declares no query parameters), and the selected proposal's
 * detail area.
 *
 * A container: it injects the one list read and derives every view model the
 * presentational {@link FleetProposalList} needs — neither it nor the detail area
 * injects a query of its own. The class chips come from the fetched data (Decision
 * 2: `class` is the deployment's own opaque vocabulary, never a hardcoded list).
 * Selection follows `gardening-routines-page.ts`'s own explicit-pick-else-first-row
 * shape, re-derived against the *filtered* set so a filter change never strands the
 * selection on a row no longer shown.
 *
 * The detail area's real content — case, closure record, evidence — is a later
 * phase's own `FleetProposalPanel`; this phase renders only its "select a proposal"
 * rest state.
 */
@Component({
  selector: 'app-gardening-proposals-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetProposalList, KitAsyncState, KitChips],
  templateUrl: './gardening-proposals-page.html',
  styleUrl: './gardening-proposals-page.css',
})
export class GardeningProposalsPage {
  private readonly proposalsQuery = injectHubGardenProposalsQuery();

  private readonly proposals = computed<readonly GardenProposalView[]>(() => this.proposalsQuery.data() ?? []);

  protected readonly waitingFilter = signal<WaitingFilter>('waiting');

  /** `null` means every class — the docket's own "All classes" chip maps to this
   * rather than a magic class string, so a real class can never collide with it. */
  protected readonly classFilter = signal<string | null>(null);

  /** Every class present in the fetched data, alphabetized, each with an "All
   * classes" chip ahead of them — never a hardcoded vocabulary (Decision 2). */
  protected readonly classChips = computed<readonly KitChipOption[]>(() => {
    const classes = Array.from(new Set(this.proposals().map((p) => p.class))).sort((a, b) => a.localeCompare(b));
    return [
      { value: ALL_CLASSES, label: 'All classes', testid: 'gardening-proposal-class-all' },
      ...classes.map((c) => ({ value: c, label: c, testid: `gardening-proposal-class-${c}` })),
    ];
  });

  protected readonly classChipValue = computed<string>(() => this.classFilter() ?? ALL_CLASSES);

  protected onClassChoose(value: string): void {
    this.classFilter.set(value === ALL_CLASSES ? null : value);
  }

  protected readonly waitingChips: readonly KitChipOption[] = [
    { value: 'waiting', label: 'Waiting', testid: 'gardening-proposal-filter-waiting' },
    { value: 'all', label: 'All', testid: 'gardening-proposal-filter-all' },
  ];

  protected onWaitingChoose(value: string): void {
    this.waitingFilter.set(value === 'all' ? 'all' : 'waiting');
  }

  /** The filtered set every other view model derives from (AC: a passed proposal
   * leaves the waiting set and stays reachable under `all`). */
  private readonly filteredProposals = computed<readonly GardenProposalView[]>(() => {
    const waitingOnly = this.waitingFilter() === 'waiting';
    const cls = this.classFilter();
    return this.proposals().filter(
      (p) => (!waitingOnly || isGardenProposalWaiting(p)) && (cls === null || p.class === cls),
    );
  });

  protected readonly listRows = computed<readonly ProposalListRowVm[]>(() =>
    this.filteredProposals().map((p) => ({
      proposalId: p.proposal_id,
      title: p.title,
      proposalClass: p.class,
      waiting: isGardenProposalWaiting(p),
    })),
  );

  protected readonly listState = computed<KitAsyncStateValue>(() =>
    asyncState(this.proposalsQuery, this.listRows().length === 0),
  );

  /** The operator's explicit pick, `null` until one is made. */
  private readonly explicitSelection = signal<string | null>(null);

  /** The effective selection: the explicit pick if it still names a row in the
   * *filtered* set, else the first filtered row — never a stale id from a proposal a
   * filter change (or a closure) has since excluded. */
  protected readonly selectedId = computed<string | null>(() => {
    const rows = this.filteredProposals();
    const explicit = this.explicitSelection();
    if (explicit !== null && rows.some((p) => p.proposal_id === explicit)) return explicit;
    return rows[0]?.proposal_id ?? null;
  });

  protected select(proposalId: string): void {
    this.explicitSelection.set(proposalId);
  }
}
