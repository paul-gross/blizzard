import type { FindingDetail } from '../core/finding.domain';
import { type FindingPanelVm } from './finding-panel';
import { type ProposalWorkItemVm } from '../proposals/proposal-panel';

/** The loaded finding, but only while it is the one the route names — `null` on the
 * bare route, and `null` while a read for a previous id is still what is cached. */
export function findingForSelection(
  id: string | null,
  finding: FindingDetail | null,
): FindingDetail | null {
  if (id === null) return null;
  return finding?.id === id ? finding : null;
}

/** `introducedRev` carries the finding's introducing revision verbatim — a git revision, not
 * a timestamp (`finding-panel.ts`'s own doc comment on why it never rides
 * `fleet-when`). `introducedAt` and `firstObservedAt` carry the finding's two
 * instants verbatim; `introducedAt` is null wherever the hub never resolved the
 * commit (`finding-panel.ts`'s own doc comment on what that means). */
export function findingPanelVm(
  finding: FindingDetail | null,
  workItemFor: (findingId: string) => ProposalWorkItemVm | null,
): FindingPanelVm | null {
  if (finding === null) return null;
  return {
    findingId: finding.id,
    findingClass: finding.findingClass,
    locus: finding.locus,
    state: finding.state,
    exit: finding.exit,
    observedCount: finding.observedCount,
    introducedRev: finding.introducedRev,
    introducedAt: finding.introducedAt,
    firstObservedAt: finding.firstObservedAt,
    lastSeenAt: finding.lastSeenAt,
    summary: finding.summary,
    note: finding.note,
    facts: finding.facts,
    workItem: workItemFor(finding.id),
    source: finding.source,
    severity: finding.severity,
    raisedByChunkId: finding.raisedByChunkId,
  };
}
