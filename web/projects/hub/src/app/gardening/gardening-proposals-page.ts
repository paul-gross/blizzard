import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';
import {
  asyncState,
  FleetProposalList,
  FleetProposalPanel,
  hasPermission,
  injectHubFindingsQuery,
  injectHubGardenProposalsQuery,
  injectHubWorkItemQuery,
  injectMeQuery,
  isGardenProposalWaiting,
  KitChips,
  type FindingView,
  type GardenProposalClosureView,
  type GardenProposalView,
  type KitAsyncStateValue,
  type KitChipOption,
  type ProposalClosureVm,
  type ProposalEvidenceRowVm,
  type ProposalListRowVm,
  type ProposalPanelVm,
  type ProposalWorkItemVm,
} from 'fleet';

import { GardeningProposalAcceptDialog } from './gardening-proposal-accept-dialog';
import { GardeningProposalPassDialog } from './gardening-proposal-pass-dialog';

/** The docket's waiting filter — a proposal not yet closed, or every proposal
 * regardless of closure. */
type WaitingFilter = 'waiting' | 'all';

const ALL_CLASSES = 'all';

/** Every real class chip's value carries this prefix, so it can never collide with
 * {@link ALL_CLASSES} no matter what a deployment names a class (`class` is opaque,
 * deployment-chosen vocabulary — Decision 2) — a class literally named `all` is a
 * real possibility, not a contrived one, and `KitChips` tracks and selects by
 * `value` alone. The class chip's `testid` carries its own `-item-` guard against the
 * same collision, one prefix protecting each of the two identifiers `KitChips` reads
 * off an option. */
const CLASS_VALUE_PREFIX = 'class:';

/**
 * The `/gardening/proposals` sub-tab
 * (`blizzard-product:/plans/garden/user-interface.md` §The docket) — the proposal
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
 * The selected proposal's own record already carries its full case and closure — the
 * one list read returns every `GardenProposalView` field, so the detail area needs no
 * second by-id fetch of its own (Decision 1's client-side-filtering spirit applied to
 * selection too). Its evidence is different: a proposal carries finding *ids* only, so
 * this container fans those out live through `injectHubFindingsQuery` (Decision 3),
 * and, for an accepted-and-minted proposal, resolves the linked work item through its
 * closure's `source`/`ref` pointer (Decision 4) via `injectHubWorkItemQuery`.
 *
 * Owns the two closing dialogs' own dialog-open signals (Decision 6: both verbs gate
 * on `chunk:control`, resolved here through `injectMeQuery` + `hasPermission` and
 * forwarded to the panel as `canControl`), `gardening-routines-page.ts`'s own
 * `runningRoutine` shape.
 */
@Component({
  selector: 'app-gardening-proposals-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetProposalList, FleetProposalPanel, GardeningProposalAcceptDialog, GardeningProposalPassDialog, KitChips],
  templateUrl: './gardening-proposals-page.html',
  styleUrl: './gardening-proposals-page.css',
})
export class GardeningProposalsPage {
  private readonly proposalsQuery = injectHubGardenProposalsQuery();
  private readonly meQuery = injectMeQuery();

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
    this.classFilter.set(value === ALL_CLASSES ? null : value.slice(CLASS_VALUE_PREFIX.length));
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

  /** The selected row's own full record — already carried by the one list read, so
   * this is a lookup, never a second fetch. */
  private readonly selectedProposal = computed<GardenProposalView | null>(
    () => this.proposals().find((p) => p.proposal_id === this.selectedId()) ?? null,
  );

  /** Panel state branches on selection before ever consulting the list read's own
   * async state (`bzh:frontend-empty-state-gated`) — once something is selected its
   * record is already in hand, synchronously, from {@link proposals}. */
  protected readonly panelState = computed<KitAsyncStateValue>(() =>
    this.selectedId() === null ? asyncState(this.proposalsQuery, true) : 'ready',
  );

  private readonly findingsQuery = injectHubFindingsQuery(() => this.selectedProposal()?.findings ?? []);

  /** The (source, ref) pair naming an accepted-and-minted proposal's linked work
   * item — `null` for a waiting, passed, or accepted-and-declined proposal, so the
   * work-item query stays disabled for all three. */
  private readonly acceptedItemPointer = computed<{ source: string; ref: string } | null>(() => {
    const closure = this.selectedProposal()?.closure;
    if (closure?.closure !== 'accepted' || closure.item_outcome !== 'minted') return null;
    return { source: closure.source!, ref: closure.ref! };
  });

  private readonly workItemQuery = injectHubWorkItemQuery(
    () => this.acceptedItemPointer()?.source ?? null,
    () => this.acceptedItemPointer()?.ref ?? null,
  );

  /** The accepted-and-minted work item, resolved for display — `null` while the
   * read is still in flight, so a loading window never shows a synthesized label
   * that could pass for resolved data; once settled, `label`/`webUrl` come off the
   * real record, or the bare pointer once the read has genuinely failed (the item
   * is gone), and `web_url` alone reads `null` once the chunk is merely terminal. */
  private readonly workItemVm = computed<ProposalWorkItemVm | null>(() => {
    const pointer = this.acceptedItemPointer();
    if (pointer === null || this.workItemQuery.isPending()) return null;
    const item = this.workItemQuery.data();
    return { label: item?.label ?? `${pointer.source}:${pointer.ref}`, webUrl: item?.web_url ?? null };
  });

  private closureVm(closure: GardenProposalClosureView): ProposalClosureVm {
    if (closure.closure === 'passed') {
      return { kind: 'passed', closedBy: closure.closed_by, closedAt: closure.closed_at, reason: closure.reason };
    }
    return {
      kind: 'accepted',
      closedBy: closure.closed_by,
      closedAt: closure.closed_at,
      reason: closure.reason,
      workItem: closure.item_outcome === 'minted' ? this.workItemVm() : null,
    };
  }

  protected readonly panelVm = computed<ProposalPanelVm | null>(() => {
    const proposal = this.selectedProposal();
    if (proposal === null) return null;
    return {
      proposalId: proposal.proposal_id,
      routineName: proposal.routine_name,
      proposalClass: proposal.class,
      title: proposal.title,
      body: proposal.body,
      closure: proposal.closure ? this.closureVm(proposal.closure) : null,
    };
  });

  private readonly evidenceFindings = computed<readonly FindingView[]>(() => this.findingsQuery.data() ?? []);

  protected readonly evidenceRows = computed<readonly ProposalEvidenceRowVm[]>(() => {
    const workItem = this.workItemVm();
    return this.evidenceFindings().map((f) => ({
      findingId: f.finding_id,
      locus: f.locus,
      summary: f.summary,
      live: f.live,
      workItem,
    }));
  });

  protected readonly evidenceState = computed<KitAsyncStateValue>(() =>
    asyncState(this.findingsQuery, this.evidenceFindings().length === 0),
  );

  /** Whether the current identity may pass or accept (`chunk:control` — the same
   * permission `garden_proposals.py`'s two closing routes require server-side);
   * `null`/pending resolves to `false`, `gardening-routines-page.ts`'s own
   * `canEditScopes` shape. */
  protected readonly canControl = computed(() => hasPermission(this.meQuery.data(), 'chunk:control'));

  /** The proposal the Pass dialog is open against — `null` closes it. Only the
   * panel's own `pass` output ever sets it, so it can only ever name the
   * already-selected, still-waiting proposal. */
  protected readonly passingProposal = signal<GardenProposalView | null>(null);

  /** The proposal the Accept dialog is open against — `null` closes it, the same
   * shape as {@link passingProposal}. */
  protected readonly acceptingProposal = signal<GardenProposalView | null>(null);

  protected openPass(): void {
    this.passingProposal.set(this.selectedProposal());
  }

  protected openAccept(): void {
    this.acceptingProposal.set(this.selectedProposal());
  }

  protected closePass(): void {
    this.passingProposal.set(null);
  }

  protected closeAccept(): void {
    this.acceptingProposal.set(null);
  }
}
