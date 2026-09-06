import { ChangeDetectionStrategy, Component, computed, effect, inject } from '@angular/core';
import { Router, RouterOutlet } from '@angular/router';
import {
  asyncState,
  FleetProposalList,
  injectHubGardenProposalsQuery,
  isGardenProposalWaiting,
  KitChips,
  type GardenProposalView,
  type KitAsyncStateValue,
  type KitChipOption,
  type ProposalListRowVm,
} from 'fleet';

import { injectChildRouteParam, injectQueryFilters } from '../route-state';

const ALL_CLASSES = 'all';

/** Every real class chip's value carries this prefix, so it can never collide with
 * {@link ALL_CLASSES} no matter what a deployment names a class (`class` is opaque,
 * deployment-chosen vocabulary — Decision 2) — a class literally named `all` is a
 * real possibility, not a contrived one, and `KitChips` tracks and selects by
 * `value` alone. The class chip's `testid` carries its own `-item-` guard against the
 * same collision, one prefix protecting each of the two identifiers `KitChips` reads
 * off an option. */
const CLASS_VALUE_PREFIX = 'class:';

/** The docket's waiting filter as it rides the URL: absent is the default (only
 * proposals not yet closed), and this one value widens it to every proposal. */
const SHOW_ALL = 'all';

/** The routine chip row's "All routines" value. Unlike `class`, `routine_name` is
 * not opaque deployment vocabulary — it names one of blizzard's own gardening
 * routines — so it needs no `CLASS_VALUE_PREFIX`-style collision guard. */
const ALL_ROUTINES = 'all';

/**
 * The `/gardening/proposals` sub-tab
 * (`blizzard-product:/plans/garden/user-interface.md` §The docket) — the proposal
 * docket, filtered client-side by waiting state, by class, and by routine (Decision
 * 1: `GET /api/garden-proposals` declares no query parameters), beside a
 * `<router-outlet>` holding whichever proposal the URL names
 * (`gardening-proposal-detail.ts`).
 *
 * `gardening-scopes-page.ts`'s own parent-list/child-detail shape. All three
 * filters live in the query string (`route-state.ts`), so a pick survives a row
 * click and a filtered docket is a link the operator can hand somebody.
 *
 * A container: it injects the one list read and derives the rows the
 * presentational {@link FleetProposalList} renders. The class and routine chips
 * both come from the fetched data (Decision 2: `class` is the deployment's own
 * opaque vocabulary, never a hardcoded list; `routine_name` likewise, though it is
 * blizzard's own vocabulary rather than the deployment's).
 *
 * This is the one tab whose bare route does not rest on an empty pane:
 * {@link reconcileSelection} sends it to the first row of the *filtered* set. The
 * docket is a work queue, and arriving at it with nothing to read would make the
 * operator click before reading anything — but the redirect is a real navigation
 * rather than a silent in-component default, so the URL always names exactly what
 * the panel is showing, and a reload or a shared link resurrects the same view.
 */
@Component({
  selector: 'app-gardening-proposals-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetProposalList, KitChips, RouterOutlet],
  templateUrl: './gardening-proposals-page.html',
  styleUrl: './gardening-proposals-page.css',
})
export class GardeningProposalsPage {
  private readonly router = inject(Router);
  private readonly url = injectQueryFilters();
  private readonly proposalsQuery = injectHubGardenProposalsQuery();

  private readonly proposals = computed<readonly GardenProposalView[]>(() => this.proposalsQuery.data() ?? []);

  /** The `proposalId` the active detail child names (`route-state.ts`). */
  protected readonly proposalId = injectChildRouteParam('proposalId');

  private readonly waitingOnly = computed<boolean>(() => this.url.read('show') !== SHOW_ALL);

  /** `null` means every class — the docket's own "All classes" chip drops the param
   * rather than naming a magic class string, so a real class can never collide. */
  private readonly classFilter = computed<string | null>(() => this.url.read('class'));

  /** Every class present in the fetched data, alphabetized, each with an "All
   * classes" chip ahead of them — never a hardcoded vocabulary (Decision 2). */
  protected readonly classChips = computed<readonly KitChipOption[]>(() => {
    const classes = Array.from(new Set(this.proposals().map((p) => p.class))).sort((a, b) => a.localeCompare(b));
    return [
      { value: ALL_CLASSES, label: 'All classes', testid: 'gardening-proposal-class-all' },
      ...classes.map((c) => ({
        value: CLASS_VALUE_PREFIX + c,
        label: c,
        testid: `gardening-proposal-class-item-${c}`,
      })),
    ];
  });

  protected readonly classChipValue = computed<string>(() => {
    const cls = this.classFilter();
    return cls === null ? ALL_CLASSES : CLASS_VALUE_PREFIX + cls;
  });

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
   * {@link classChips}. */
  protected readonly routineChips = computed<readonly KitChipOption[]>(() => {
    const routines = Array.from(new Set(this.proposals().map((p) => p.routine_name))).sort((a, b) =>
      a.localeCompare(b),
    );
    return [
      { value: ALL_ROUTINES, label: 'All routines', testid: 'gardening-proposal-routine-all' },
      ...routines.map((r) => ({
        value: r,
        label: r,
        testid: `gardening-proposal-routine-item-${r}`,
      })),
    ];
  });

  protected readonly routineChipValue = computed<string>(() => this.routineFilter() ?? ALL_ROUTINES);

  protected onRoutineChoose(value: string): void {
    this.url.patch({ routine: value === ALL_ROUTINES ? null : value });
  }

  protected readonly waitingChipValue = computed<string>(() => (this.waitingOnly() ? 'waiting' : SHOW_ALL));

  protected onWaitingChoose(value: string): void {
    this.url.patch({ show: value === SHOW_ALL ? SHOW_ALL : null });
  }

  /** The filtered set every other view model derives from (AC: a passed proposal
   * leaves the waiting set and stays reachable under `all`). */
  private readonly filteredProposals = computed<readonly GardenProposalView[]>(() => {
    const waitingOnly = this.waitingOnly();
    const cls = this.classFilter();
    const routine = this.routineFilter();
    return this.proposals().filter(
      (p) =>
        (!waitingOnly || isGardenProposalWaiting(p)) &&
        (cls === null || p.class === cls) &&
        (routine === null || p.routine_name === routine),
    );
  });

  protected readonly listRows = computed<readonly ProposalListRowVm[]>(() =>
    this.filteredProposals().map((p) => ({
      proposalId: p.proposal_id,
      title: p.title,
      proposalClass: p.class,
      waiting: isGardenProposalWaiting(p),
      createdAt: p.created_at,
    })),
  );

  protected readonly listState = computed<KitAsyncStateValue>(() =>
    asyncState(this.proposalsQuery, this.listRows().length === 0),
  );

  protected select(proposalId: string): void {
    void this.router.navigate(['/gardening', 'proposals', proposalId], { queryParamsHandling: 'preserve' });
  }

  constructor() {
    effect(() => this.reconcileSelection());
  }

  /**
   * Keeps the URL's proposal and the docket's filters agreeing, in both
   * directions: a selection the current filters exclude — or a bare route on a
   * docket that has rows — resolves to the first filtered row, and only a docket
   * with nothing in it leaves the route bare.
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
    const first = rows[0]?.proposal_id ?? null;
    if (routed === null && first === null) return;
    void this.router.navigate(
      first === null ? ['/gardening', 'proposals'] : ['/gardening', 'proposals', first],
      { replaceUrl: true, queryParamsHandling: 'preserve' },
    );
  }
}
