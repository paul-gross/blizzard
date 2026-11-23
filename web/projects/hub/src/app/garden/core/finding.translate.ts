// bzh:frontend-domain-boundary — the one place the finding wire is read; an unforeseen vocabulary value becomes an
// `Unlisted` here and renders as itself (bzh:frontend-open-vocabulary).
import { type FindingDetailView, type FindingFactView, type FindingsPageView, type FindingView, hubApi, openLookup } from 'fleet';

import type {
  Finding,
  FindingDetail,
  FindingExit,
  FindingFact,
  FindingFactKind,
  FindingPage,
  FindingSeverity,
  FindingSource,
  FindingState,
} from './finding.domain';
import { unlisted, type Vocabulary } from './vocabulary.domain';

const stateOf = openLookup<hubApi.FindingState, Vocabulary<FindingState>>(
  {
    [hubApi.FindingState.LIVE]: 'live',
    [hubApi.FindingState.GONE]: 'gone',
    [hubApi.FindingState.DELIVERED]: 'delivered',
    [hubApi.FindingState.RESOLVED]: 'resolved',
    [hubApi.FindingState.GONE_CONFIRMED]: 'gone-confirmed',
    [hubApi.FindingState.WONT_FIX]: 'wont-fix',
    [hubApi.FindingState.NOT_A_FINDING]: 'not-a-finding',
    [hubApi.FindingState.SUPERSEDED]: 'superseded',
  },
  unlisted,
);

const severityOf = openLookup<hubApi.FindingSeverity, Vocabulary<FindingSeverity>>(
  { [hubApi.FindingSeverity.BLOCKING]: 'blocking', [hubApi.FindingSeverity.SHOULD_FIX]: 'should-fix' },
  unlisted,
);

const sourceOf = openLookup<hubApi.FindingSource, Vocabulary<FindingSource>>(
  { [hubApi.FindingSource.ROUTINE]: 'routine', [hubApi.FindingSource.REVIEW]: 'review' },
  unlisted,
);

const exitOf = openLookup<hubApi.FindingExit, Vocabulary<FindingExit>>(
  { [hubApi.FindingExit.OUTFLOW]: 'outflow', [hubApi.FindingExit.WITHDRAWN]: 'withdrawn' },
  unlisted,
);

const factKindOf = openLookup<hubApi.FindingFactKind, Vocabulary<FindingFactKind>>(
  {
    [hubApi.FindingFactKind.ADD]: 'add',
    [hubApi.FindingFactKind.OBSERVED]: 'observed',
    [hubApi.FindingFactKind.GONE]: 'gone',
    [hubApi.FindingFactKind.DELIVERED]: 'delivered',
    [hubApi.FindingFactKind.RESOLVED]: 'resolved',
    [hubApi.FindingFactKind.GONE_CONFIRMED]: 'gone-confirmed',
    [hubApi.FindingFactKind.WONT_FIX]: 'wont-fix',
    [hubApi.FindingFactKind.NOT_A_FINDING]: 'not-a-finding',
    [hubApi.FindingFactKind.SUPERSEDED]: 'superseded',
    [hubApi.FindingFactKind.REOPENED]: 'reopened',
  },
  unlisted,
);

/** A finding as the hub serves it, read into the domain. A finding the hub names no source for reads as routine-raised. */
export function fromFindingView(wire: FindingView): Finding {
  return {
    id: wire.finding_id,
    findingClass: wire.class,
    locus: wire.locus,
    summary: wire.summary,
    note: wire.note ?? null,
    state: stateOf(wire.state),
    exit: wire.exit == null ? null : exitOf(wire.exit),
    observedCount: wire.observed_count,
    introducedRev: wire.introduced ?? null,
    introducedAt: wire.introduced_at ?? null,
    firstObservedAt: wire.first_observed_at ?? null,
    lastSeenAt: wire.last_seen_at ?? null,
    routineSlug: wire.routine_name ?? null,
    scopeSlug: wire.scope_slug,
    source: sourceOf(wire.source ?? hubApi.FindingSource.ROUTINE),
    severity: wire.severity == null ? null : severityOf(wire.severity),
    raisedByChunkId: wire.raised_by_chunk_id ?? null,
  };
}

/** One fact of a finding's chain, read into the domain. */
export function fromFindingFactView(wire: FindingFactView): FindingFact {
  return {
    kind: factKindOf(wire.kind),
    note: wire.note ?? null,
    actor: wire.actor ?? null,
    recordedAt: wire.recorded_at,
    proposalId: wire.proposal_id ?? null,
    supersededBy: wire.superseded_by ?? null,
  };
}

/** A finding read on its own, its fact chain kept in the hub's oldest-first order. */
export function fromFindingDetailView(wire: FindingDetailView): FindingDetail {
  return { ...fromFindingView(wire), facts: wire.facts.map(fromFindingFactView) };
}

/** One page of the triage bucket, read into the domain. */
export function fromFindingsPageView(wire: FindingsPageView): FindingPage {
  return { findings: (wire.findings ?? []).map(fromFindingView), nextCursor: wire.next_cursor ?? null };
}
