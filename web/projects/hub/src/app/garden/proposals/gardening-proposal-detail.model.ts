import {
  asyncState,
  isPendingFor,
  type AsyncStateQuery,
  type FindingView,
  type GardenProposalClosureView,
  type GardenProposalView,
  type KitAsyncStateValue,
  type WorkItemView,
} from 'fleet';
import { type FindingExitVars } from '../core/finding.mutations';
import {
  type ProposalClosureVm,
  type ProposalEvidenceRowVm,
  type ProposalOriginVm,
  type ProposalPanelVm,
  type ProposalWorkItemVm,
} from './proposal-panel';

/** The (source, ref) pair naming an accepted-and-minted proposal's linked work item. */
export interface AcceptedItemPointer {
  readonly source: string;
  readonly ref: string;
}

/** The routed proposal's own full record — already carried by the one list read, so
 * this is a lookup, never a second fetch. `null` on the bare route or an id the
 * docket does not hold. */
export function proposalById(
  proposals: readonly GardenProposalView[],
  id: string | null,
): GardenProposalView | null {
  return id === null ? null : (proposals.find((p) => p.proposal_id === id) ?? null);
}

/** Panel state branches on selection before ever consulting the list read's own
 * async state (`bzh:frontend-empty-state-gated`) — once something is selected its
 * record is already in hand, synchronously, from the list read. */
export function proposalPanelState(
  selected: GardenProposalView | null,
  query: AsyncStateQuery,
): KitAsyncStateValue {
  return selected === null ? asyncState(query, true) : 'ready';
}

/** The pointer to an accepted-and-minted proposal's linked work item — `null` for a
 * waiting, passed, or accepted-and-declined proposal, so the work-item query stays
 * disabled for all three. */
export function acceptedItemPointer(
  closure: GardenProposalClosureView | null | undefined,
): AcceptedItemPointer | null {
  if (closure?.closure !== 'accepted' || closure.item_outcome !== 'minted') return null;
  return { source: closure.source!, ref: closure.ref! };
}

/** The accepted-and-minted work item, resolved for display — `null` while the
 * read is still in flight, so a loading window never shows a synthesized label
 * that could pass for resolved data; once settled, `label`/`webUrl` come off the
 * real record, or the bare pointer once the read has genuinely failed (the item
 * is gone), and `web_url` alone reads `null` once the chunk is merely terminal. */
export function proposalWorkItemVm(
  pointer: AcceptedItemPointer | null,
  isPending: boolean,
  item: Pick<WorkItemView, 'label' | 'web_url'> | undefined,
): ProposalWorkItemVm | null {
  if (pointer === null || isPending) return null;
  return { label: item?.label ?? `${pointer.source}:${pointer.ref}`, webUrl: item?.web_url ?? null };
}

/** Who raised the proposal: an operator by name, or a routine run. */
export function proposalOriginVm(proposal: GardenProposalView): ProposalOriginVm {
  if (proposal.origin === 'operator') {
    return { kind: 'operator', createdBy: proposal.created_by!, routineName: proposal.routine_name };
  }
  return { kind: 'routine-run', routineName: proposal.routine_name! };
}

/** How the proposal closed; only an accepted-and-minted closure carries the work item. */
export function proposalClosureVm(
  closure: GardenProposalClosureView,
  workItem: ProposalWorkItemVm | null,
): ProposalClosureVm {
  if (closure.closure === 'passed') {
    return { kind: 'passed', closedBy: closure.closed_by, closedAt: closure.closed_at, reason: closure.reason };
  }
  return {
    kind: 'accepted',
    closedBy: closure.closed_by,
    closedAt: closure.closed_at,
    reason: closure.reason,
    workItem: closure.item_outcome === 'minted' ? workItem : null,
  };
}

/** The panel's view model for the selected proposal, or `null` with nothing selected. */
export function proposalPanelVm(
  proposal: GardenProposalView | null,
  workItem: ProposalWorkItemVm | null,
): ProposalPanelVm | null {
  if (proposal === null) return null;
  return {
    proposalId: proposal.proposal_id,
    origin: proposalOriginVm(proposal),
    proposalClass: proposal.class,
    title: proposal.title,
    body: proposal.body,
    closure: proposal.closure ? proposalClosureVm(proposal.closure, workItem) : null,
    createdAt: proposal.created_at,
    hasFindings: proposal.findings.length > 0,
  };
}

/** One evidence row per live-read finding; a row whose finding id is among the
 * in-flight triage variables reads `pending` (`bzh:frontend-pending-override`). */
export function proposalEvidenceRows(
  findings: readonly FindingView[],
  workItem: ProposalWorkItemVm | null,
  pendingTriage: readonly FindingExitVars[],
): readonly ProposalEvidenceRowVm[] {
  return findings.map((f) => ({
    findingId: f.finding_id,
    locus: f.locus,
    summary: f.summary,
    state: f.state,
    exit: f.exit ?? null,
    workItem,
    pending: isPendingFor(pendingTriage, (vars) => vars.findingIds.includes(f.finding_id)),
  }));
}
