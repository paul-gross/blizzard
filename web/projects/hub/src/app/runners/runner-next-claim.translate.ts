import type { ChunkDetail, hubApi, UnknownHostValue } from 'fleet';
import { laneStatus, openLookup } from 'fleet/shell';
import { fromUrgencyRef } from '../core/urgency.translate';
import type {
  ClaimPassReason,
  NextClaim,
  NextClaimCandidate,
  NextClaimPassed,
  NextClaimScenario,
  NextClaimWalk,
  PrerequisiteStatus,
} from './runner-next-claim.domain';

const reasons = openLookup<hubApi.ClaimPassReason, ClaimPassReason>(
  { blocked: 'blocked', ineligible: 'ineligible' },
  (raw): UnknownHostValue => ({ kind: 'unknown', raw }),
);

function toCandidate(wire: hubApi.NextClaimEntry): NextClaimCandidate {
  return {
    chunk_id: wire.chunk_id,
    graph_id: wire.graph_id,
    position: wire.position,
    project_id: wire.project_id ?? null,
    project_name: wire.project_name ?? null,
    project_slug: wire.project_slug ?? null,
    urgency: wire.urgency ? fromUrgencyRef(wire.urgency) : null,
  };
}

function toPassed(wire: hubApi.NextClaimPassedEntry): NextClaimPassed {
  return {
    ...toCandidate(wire),
    reason: reasons(wire.reason),
    prerequisite: wire.prerequisite ?? null,
    prerequisite_count: wire.prerequisite_count,
  };
}

function toWalk(wire: hubApi.NextClaimResponse | hubApi.NextClaimScenario): NextClaimWalk {
  return {
    next: wire.next ? toCandidate(wire.next) : null,
    passed: wire.passed.map(toPassed),
    passed_total: wire.passed_total,
    unserved: wire.unserved,
    if_holding: wire.if_holding ? toCandidate(wire.if_holding) : null,
    held_at: wire.held_at.map(toPassed),
  };
}

function toScenario(wire: hubApi.NextClaimScenario): NextClaimScenario {
  return { ...toWalk(wire), excluded: wire.excluded };
}

/** A hub that predates the no-target alternative omits it; that reads as not reported, the same as `null`. */
export function toNextClaim(wire: hubApi.NextClaimResponse): NextClaim {
  return {
    ...toWalk(wire),
    without_repository_targets: wire.without_repository_targets ? toScenario(wire.without_repository_targets) : null,
  };
}

export function toPrerequisiteStatus(wire: ChunkDetail): PrerequisiteStatus {
  return { chunk_id: wire.chunk_id, laneStatus: laneStatus(wire.status) };
}
