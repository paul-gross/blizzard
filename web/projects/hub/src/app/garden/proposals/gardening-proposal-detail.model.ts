import {
  asyncState,
  isPendingFor,
  type AsyncStateQuery,
  type KitAsyncStateValue,
} from 'fleet';
import type { Finding } from '../core/finding.domain';
import type { Proposal, ProposalClosure } from '../core/proposal.domain';
import type { Routine } from '../core/routine.domain';
import { vocabularyText } from '../core/vocabulary.domain';
import type { WorkItem } from '../core/work-item.domain';
import { type FindingExitVars } from '../core/finding.mutations';
import { routineDisplayName } from '../core/routine-display-name';
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
  proposals: readonly Proposal[],
  id: string | null,
): Proposal | null {
  return id === null ? null : (proposals.find((p) => p.id === id) ?? null);
}

/** Panel state branches on selection before ever consulting the list read's own
 * async state (`bzh:frontend-empty-state-gated`) — once something is selected its
 * record is already in hand, synchronously, from the list read. */
export function proposalPanelState(
  selected: Proposal | null,
  query: AsyncStateQuery,
): KitAsyncStateValue {
  return selected === null ? asyncState(query, true) : 'ready';
}

/** The pointer to an accepted-and-minted proposal's linked work item — `null` for a
 * waiting, passed, or accepted-and-declined proposal, so the work-item query stays
 * disabled for all three. */
export function acceptedItemPointer(
  closure: ProposalClosure | null | undefined,
): AcceptedItemPointer | null {
  if (closure?.kind !== 'accepted' || closure.itemOutcome !== 'minted') return null;
  return { source: closure.source!, ref: closure.ref! };
}

/** The accepted-and-minted work item, resolved for display — `null` while the
 * read is still in flight, so a loading window never shows a synthesized label
 * that could pass for resolved data; once settled, `label`/`webUrl` come off the
 * real record, or the bare pointer once the read has genuinely failed (the item
 * is gone), and `webUrl` alone reads `null` once the chunk is merely terminal. */
export function proposalWorkItemVm(
  pointer: AcceptedItemPointer | null,
  isPending: boolean,
  item: Pick<WorkItem, 'label' | 'webUrl'> | undefined,
): ProposalWorkItemVm | null {
  if (pointer === null || isPending) return null;
  return { label: item?.label ?? `${pointer.source}:${pointer.ref}`, webUrl: item?.webUrl ?? null };
}

/** Who raised the proposal: an operator by name, or a routine run — the routine read by its display name. */
export function proposalOriginVm(proposal: Proposal, routines: readonly Routine[] | undefined): ProposalOriginVm {
  const slug = proposal.routineSlug;
  switch (proposal.origin) {
    case 'operator':
      return {
        kind: 'operator',
        createdBy: proposal.createdBy!,
        routineName: slug === null ? null : routineDisplayName(slug, routines),
      };
    case 'routine-run':
      return { kind: 'routine-run', routineName: routineDisplayName(slug!, routines) };
    default:
      return { kind: 'unlisted', raw: vocabularyText(proposal.origin) };
  }
}

/** How the proposal closed; only an accepted-and-minted closure carries the work item. */
export function proposalClosureVm(
  closure: ProposalClosure,
  workItem: ProposalWorkItemVm | null,
): ProposalClosureVm {
  switch (closure.kind) {
    case 'passed':
      return { kind: 'passed', closedBy: closure.closedBy, closedAt: closure.closedAt, reason: closure.reason };
    case 'accepted':
      return {
        kind: 'accepted',
        closedBy: closure.closedBy,
        closedAt: closure.closedAt,
        reason: closure.reason,
        workItem: closure.itemOutcome === 'minted' ? workItem : null,
      };
    default:
      return { kind: 'unlisted', raw: vocabularyText(closure.kind), closedBy: closure.closedBy, closedAt: closure.closedAt, reason: closure.reason };
  }
}

/** The panel's view model for the selected proposal, or `null` with nothing selected. */
export function proposalPanelVm(
  proposal: Proposal | null,
  workItem: ProposalWorkItemVm | null,
  routines: readonly Routine[] | undefined,
): ProposalPanelVm | null {
  if (proposal === null) return null;
  return {
    proposalId: proposal.id,
    origin: proposalOriginVm(proposal, routines),
    proposalClass: proposal.proposalClass,
    title: proposal.title,
    body: proposal.body,
    closure: proposal.closure ? proposalClosureVm(proposal.closure, workItem) : null,
    createdAt: proposal.createdAt,
    hasFindings: proposal.findingIds.length > 0,
  };
}

/** One evidence row per live-read finding; a row whose finding id is among the
 * in-flight triage variables reads `pending` (`bzh:frontend-pending-override`). */
export function proposalEvidenceRows(
  findings: readonly Finding[],
  workItem: ProposalWorkItemVm | null,
  pendingTriage: readonly FindingExitVars[],
): readonly ProposalEvidenceRowVm[] {
  return findings.map((f) => ({
    findingId: f.id,
    locus: f.locus,
    summary: f.summary,
    state: f.state,
    exit: f.exit,
    workItem,
    pending: isPendingFor(pendingTriage, (vars) => vars.findingIds.includes(f.id)),
  }));
}
