// bzh:frontend-domain-boundary — the one place the garden proposal wire is read; an unforeseen vocabulary value
// becomes an `Unlisted` here and renders as itself (bzh:frontend-open-vocabulary).
import { type GardenProposalClosureView, type GardenProposalView, hubApi, openLookup } from 'fleet';

import type { Proposal, ProposalClosure, ProposalClosureKind, ProposalItemOutcome, ProposalOrigin } from './proposal.domain';
import { unlisted, type Vocabulary } from './vocabulary.domain';

/** Who raised a proposal, as the domain holds it. */
export const proposalOriginOf = openLookup<hubApi.GardenProposalOrigin, Vocabulary<ProposalOrigin>>(
  { [hubApi.GardenProposalOrigin.ROUTINE_RUN]: 'routine-run', [hubApi.GardenProposalOrigin.OPERATOR]: 'operator' },
  unlisted,
);

const closureKindOf = openLookup<hubApi.GardenProposalClosureKind, Vocabulary<ProposalClosureKind>>(
  { [hubApi.GardenProposalClosureKind.PASSED]: 'passed', [hubApi.GardenProposalClosureKind.ACCEPTED]: 'accepted' },
  unlisted,
);

const itemOutcomeOf = openLookup<hubApi.GardenProposalItemOutcome, Vocabulary<ProposalItemOutcome>>(
  { [hubApi.GardenProposalItemOutcome.MINTED]: 'minted', [hubApi.GardenProposalItemOutcome.DECLINED]: 'declined' },
  unlisted,
);

/** A proposal's closure record, read into the domain. */
export function fromGardenProposalClosureView(wire: GardenProposalClosureView): ProposalClosure {
  return {
    kind: closureKindOf(wire.closure),
    closedBy: wire.closed_by,
    closedAt: wire.closed_at,
    reason: wire.reason ?? null,
    itemOutcome: wire.item_outcome == null ? null : itemOutcomeOf(wire.item_outcome),
    source: wire.source ?? null,
    ref: wire.ref ?? null,
  };
}

/** A garden proposal as the hub serves it, read into the domain. */
export function fromGardenProposalView(wire: GardenProposalView): Proposal {
  return {
    id: wire.proposal_id,
    title: wire.title,
    body: wire.body,
    proposalClass: wire.class,
    origin: proposalOriginOf(wire.origin),
    createdBy: wire.created_by ?? null,
    createdAt: wire.created_at,
    routineSlug: wire.routine_name ?? null,
    projectId: wire.project_id ?? null,
    findingIds: wire.findings,
    closure: wire.closure == null ? null : fromGardenProposalClosureView(wire.closure),
  };
}
