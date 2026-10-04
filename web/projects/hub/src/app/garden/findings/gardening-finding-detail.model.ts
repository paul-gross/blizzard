import { type FindingDetailView } from 'fleet';
import { type FindingPanelVm } from './finding-panel';
import { type ProposalWorkItemVm } from '../proposals/proposal-panel';

/** The loaded finding, but only while it is the one the route names — `null` on the
 * bare route, and `null` while a read for a previous id is still what is cached. */
export function findingForSelection(
  id: string | null,
  finding: FindingDetailView | null,
): FindingDetailView | null {
  if (id === null) return null;
  return finding?.finding_id === id ? finding : null;
}

/** `introducedRev` carries `FindingView.introduced` verbatim — a git revision, not
 * a timestamp (`finding-panel.ts`'s own doc comment on why it never rides
 * `fleet-when`). `introducedAt` and `firstObservedAt` carry `FindingView`'s two
 * instants verbatim; `introducedAt` is null wherever the hub never resolved the
 * commit (`finding-panel.ts`'s own doc comment on what that means). */
export function findingPanelVm(
  finding: FindingDetailView | null,
  workItemFor: (findingId: string) => ProposalWorkItemVm | null,
): FindingPanelVm | null {
  if (finding === null) return null;
  return {
    findingId: finding.finding_id,
    findingClass: finding.class,
    locus: finding.locus,
    state: finding.state,
    exit: finding.exit ?? null,
    observedCount: finding.observed_count,
    introducedRev: finding.introduced ?? null,
    introducedAt: finding.introduced_at ?? null,
    firstObservedAt: finding.first_observed_at ?? null,
    lastSeenAt: finding.last_seen_at,
    summary: finding.summary,
    note: finding.note ?? null,
    facts: finding.facts,
    workItem: workItemFor(finding.finding_id),
    source: finding.source ?? 'routine',
    severity: finding.severity ?? null,
    raisedByChunkId: finding.raised_by_chunk_id ?? null,
  };
}
