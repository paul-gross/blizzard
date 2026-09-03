import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import {
  asyncState,
  FleetProposalPanel,
  hasPermission,
  injectHubFindingsQuery,
  injectHubGardenProposalsQuery,
  injectHubWorkItemQuery,
  injectMeQuery,
  type FindingView,
  type GardenProposalClosureView,
  type GardenProposalView,
  type KitAsyncStateValue,
  type ProposalClosureVm,
  type ProposalEvidenceRowVm,
  type ProposalPanelVm,
  type ProposalWorkItemVm,
} from 'fleet';
import { map } from 'rxjs';

import { GardeningProposalAcceptDialog } from './gardening-proposal-accept-dialog';
import { GardeningProposalPassDialog } from './gardening-proposal-pass-dialog';

/**
 * The selected proposal's own detail — the right-hand child of
 * `/gardening/proposals` (`gardening-proposals-page.ts` owns the docket beside
 * it), and where passing and accepting are dispatched from. Mounted by both of
 * that route's children, so the bare one renders the panel's own empty state; on
 * a docket with anything in it the list route navigates to a row rather than
 * leaving the operator on that bare path, so the empty state shows only on a
 * genuinely empty docket.
 *
 * The selected proposal's own record already carries its full case and closure —
 * the one list read returns every `GardenProposalView` field, so this pane needs
 * no second by-id fetch of its own (Decision 1's client-side-filtering spirit
 * applied to selection too), and reaching for that same cache-keyed read is what
 * lets it resolve the routed proposal without a seam back to the list. Its
 * evidence is different: a proposal carries finding *ids* only, so this container
 * fans those out live through `injectHubFindingsQuery` (Decision 3), and, for an
 * accepted-and-minted proposal, resolves the linked work item through its
 * closure's `source`/`ref` pointer (Decision 4) via `injectHubWorkItemQuery`.
 *
 * Owns the two closing dialogs' own dialog-open signals (Decision 6: both verbs
 * gate on `chunk:control`, resolved here through `injectMeQuery` +
 * `hasPermission` and forwarded to the panel as `canControl`).
 */
@Component({
  selector: 'app-gardening-proposal-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetProposalPanel, GardeningProposalAcceptDialog, GardeningProposalPassDialog],
  templateUrl: './gardening-proposal-detail.html',
  styleUrl: './gardening-detail-host.css',
})
export class GardeningProposalDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly proposalsQuery = injectHubGardenProposalsQuery();
  private readonly meQuery = injectMeQuery();

  private readonly proposals = computed<readonly GardenProposalView[]>(() => this.proposalsQuery.data() ?? []);

  /** The `proposalId` route param, or `null` on the bare child route. A proposal is
   * keyed by its own id (`gprop_…`, rendered compactly as `GP-…`). */
  private readonly proposalId = toSignal(this.route.paramMap.pipe(map((params) => params.get('proposalId'))), {
    initialValue: null,
  });

  /** The selected row's own full record — already carried by the one list read, so
   * this is a lookup, never a second fetch. */
  private readonly selectedProposal = computed<GardenProposalView | null>(() => {
    const id = this.proposalId();
    return id === null ? null : (this.proposals().find((p) => p.proposal_id === id) ?? null);
  });

  /** Panel state branches on selection before ever consulting the list read's own
   * async state (`bzh:frontend-empty-state-gated`) — once something is selected its
   * record is already in hand, synchronously, from {@link proposals}. */
  protected readonly panelState = computed<KitAsyncStateValue>(() =>
    this.selectedProposal() === null ? asyncState(this.proposalsQuery, true) : 'ready',
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
      createdAt: proposal.created_at,
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
   * `null`/pending resolves to `false`. */
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
