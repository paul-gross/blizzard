import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { injectPendingMutationVariables, compactRef, errorMessage, restingAsyncState, type FindingView, type GardenProposalView, type KitAsyncStateValue } from 'fleet';
import { FleetProposalPanel, type ProposalEvidenceRowVm, type ProposalEvidenceTriage, type ProposalEvidenceVerb, type ProposalPanelVm, type ProposalWorkItemVm } from './proposal-panel';
import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { injectHubFindingsQuery } from '../core/finding.query';
import { injectHubGardenProposalsQuery } from './garden-proposals.query';
import { injectHubWorkItemQuery } from '../core/work-item.query';
import { injectConfirmGoneFindingsMutation, injectNotAFindingFindingsMutation, injectResolveFindingsMutation, injectWontFixFindingsMutation, type FindingExitVars } from '../core/finding.mutations';
import { confirmGoneFindingsMutationKey, notAFindingFindingsMutationKey, resolveFindingsMutationKey, wontFixFindingsMutationKey } from '../../core/mutation-keys';
import { map } from 'rxjs';

import { GardeningProposalAcceptDialog } from './gardening-proposal-accept-dialog';
import { GardeningProposalPassDialog } from './gardening-proposal-pass-dialog';
import {
  acceptedItemPointer,
  proposalById,
  proposalEvidenceRows,
  proposalPanelState,
  proposalPanelVm,
  proposalWorkItemVm,
  type AcceptedItemPointer,
} from './gardening-proposal-detail.model';

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
 * no second by-id fetch of its own (the same client-side-filtering spirit
 * applied to selection too), and reaching for that same cache-keyed read is what
 * lets it resolve the routed proposal without a seam back to the list. Its
 * evidence is different: a proposal carries finding *ids* only, so this container
 * fans those out live through `injectHubFindingsQuery`, and, for an
 * accepted-and-minted proposal, resolves the linked work item through its
 * closure's `source`/`ref` pointer via `injectHubWorkItemQuery`.
 *
 * Owns the two closing dialogs' own dialog-open signals (both verbs
 * gate on `chunk:control`, resolved here through `injectMeQuery` +
 * `hasPermission` and forwarded to the panel as `canControl`).
 */
@Component({
  selector: 'app-gardening-proposal-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetProposalPanel, GardeningProposalAcceptDialog, GardeningProposalPassDialog],
  templateUrl: './gardening-proposal-detail.html',
  styleUrl: '../core/gardening-detail-host.css',
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
  private readonly selectedProposal = computed<GardenProposalView | null>(() =>
    proposalById(this.proposals(), this.proposalId()),
  );

  /** Branches on selection before the list read's own async state
   * (`bzh:frontend-empty-state-gated`). */
  protected readonly panelState = computed<KitAsyncStateValue>(() =>
    proposalPanelState(this.selectedProposal(), this.proposalsQuery),
  );

  private readonly findingsQuery = injectHubFindingsQuery(() => this.selectedProposal()?.findings ?? []);

  /** The (source, ref) pair naming an accepted-and-minted proposal's linked work
   * item — `null` for a waiting, passed, or accepted-and-declined proposal, so the
   * work-item query stays disabled for all three. */
  private readonly acceptedItemPointer = computed<AcceptedItemPointer | null>(() =>
    acceptedItemPointer(this.selectedProposal()?.closure),
  );

  private readonly workItemQuery = injectHubWorkItemQuery(
    () => this.acceptedItemPointer()?.source ?? null,
    () => this.acceptedItemPointer()?.ref ?? null,
  );

  /** The accepted-and-minted work item, resolved for display — `null` while its read
   * is in flight. */
  private readonly workItemVm = computed<ProposalWorkItemVm | null>(() =>
    proposalWorkItemVm(this.acceptedItemPointer(), this.workItemQuery.isPending(), this.workItemQuery.data()),
  );

  protected readonly panelVm = computed<ProposalPanelVm | null>(() =>
    proposalPanelVm(this.selectedProposal(), this.workItemVm()),
  );

  private readonly evidenceFindings = computed<readonly FindingView[]>(() => this.findingsQuery.data() ?? []);

  protected readonly evidenceRows = computed<readonly ProposalEvidenceRowVm[]>(() =>
    proposalEvidenceRows(this.evidenceFindings(), this.workItemVm(), this.pendingTriage()),
  );

  /** A proposal citing no findings leaves the findings query disabled, which reports
   * `isPending()` forever — so that case is `empty` before the helper is consulted. */
  protected readonly evidenceState = computed<KitAsyncStateValue>(() =>
    restingAsyncState(
      (this.selectedProposal()?.findings.length ?? 0) === 0,
      this.findingsQuery,
      this.evidenceFindings().length === 0,
    ),
  );

  /** The variables of every inline triage mutation still in flight, across the four
   * verbs (`bzh:frontend-pending-override`) — a row whose finding id is among them is
   * not offered its buttons again. */
  private readonly pendingTriage = computed<readonly FindingExitVars[]>(() => [
    ...this.pendingResolve(),
    ...this.pendingConfirmGone(),
    ...this.pendingWontFix(),
    ...this.pendingNotAFinding(),
  ]);
  private readonly pendingResolve = injectPendingMutationVariables<FindingExitVars>(resolveFindingsMutationKey);
  private readonly pendingConfirmGone = injectPendingMutationVariables<FindingExitVars>(confirmGoneFindingsMutationKey);
  private readonly pendingWontFix = injectPendingMutationVariables<FindingExitVars>(wontFixFindingsMutationKey);
  private readonly pendingNotAFinding = injectPendingMutationVariables<FindingExitVars>(notAFindingFindingsMutationKey);

  /** Whether the current identity may pass or accept (`chunk:control` — the same
   * permission the hub's two closing routes require server-side);
   * `null`/pending resolves to `false`. */
  protected readonly canControl = computed(() => hasPermission(this.meQuery.data(), 'chunk:control'));

  private readonly resolveFindings = injectResolveFindingsMutation();
  private readonly confirmGoneFindings = injectConfirmGoneFindingsMutation();
  private readonly wontFixFindings = injectWontFixFindingsMutation();
  private readonly notAFindingFindings = injectNotAFindingFindingsMutation();

  /** One entry per verb the evidence table offers, each closing over its own injected
   * mutation — the same by-verb dispatch table `gardening-finding-triage-dialog.ts`
   * uses, since a mutation must be injected in a field initializer and cannot be
   * picked inside the handler. `label` is what the generated note names the change
   * as. */
  private readonly evidenceMutations: Record<
    ProposalEvidenceVerb,
    {
      readonly label: string;
      readonly mutate: (
        vars: { findingIds: string[]; note: string },
        opts: { onError: (error: unknown) => void },
      ) => void;
    }
  > = {
    resolve: { label: 'resolved', mutate: (vars, opts) => this.resolveFindings.mutate(vars, opts) },
    'confirm-gone': {
      label: 'gone (confirmed)',
      mutate: (vars, opts) => this.confirmGoneFindings.mutate(vars, opts),
    },
    'wont-fix': { label: "won't fix", mutate: (vars, opts) => this.wontFixFindings.mutate(vars, opts) },
    'not-a-finding': {
      label: 'not a finding',
      mutate: (vars, opts) => this.notAFindingFindings.mutate(vars, opts),
    },
  };

  /** The most recent inline triage failure, or `null` — surfaced on the panel rather
   * than swallowed, since a quick action has no dialog left open to report into. */
  protected readonly evidenceError = signal<string | null>(null);

  /**
   * Apply one inline exit verb to one evidence row. The note is generated rather than
   * asked for: every exit route rejects a blank one (422), and the point of these buttons is a decision made in one click
   * — so the UI writes what it actually knows, which is the verb and the docket the
   * operator was reading when they chose it. The mutations invalidate the evidence
   * table's own cache (`finding.mutations.ts`), so the row's state re-renders itself
   * with no local bookkeeping here.
   */
  protected onEvidenceTriage(triage: ProposalEvidenceTriage): void {
    const proposal = this.selectedProposal();
    if (proposal === null) return;
    const entry = this.evidenceMutations[triage.verb];
    this.evidenceError.set(null);
    entry.mutate(
      {
        findingIds: [triage.findingId],
        note: `Triaged as ${entry.label} from proposal ${compactRef(proposal.proposal_id)}'s evidence.`,
      },
      { onError: (error) => this.evidenceError.set(errorMessage(error, `${triage.verb} failed.`)) },
    );
  }

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
