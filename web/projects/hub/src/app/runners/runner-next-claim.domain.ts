import type { LaneStatus, UnknownHostValue, UrgencyReference } from 'fleet';

/** Why a runner's walk passed a chunk; `UnknownHostValue` carries a reason this build cannot name. */
export type KnownClaimPassReason = 'blocked' | 'ineligible';
export type ClaimPassReason = KnownClaimPassReason | UnknownHostValue;

/** A chunk the hub's claim walk reached, at its queue position. */
export interface NextClaimCandidate {
  readonly chunk_id: string;
  readonly graph_id: string;
  readonly position: number;
  readonly project_id: string | null;
  readonly project_name: string | null;
  readonly project_slug: string | null;
  readonly urgency: UrgencyReference | null;
}

/** A chunk the walk passed, with why, and the first of the prerequisites holding it. */
export interface NextClaimPassed extends NextClaimCandidate {
  readonly reason: ClaimPassReason;
  readonly prerequisite: string | null;
  readonly prerequisite_count: number;
}

/** One account of the hub's bounded claim walk for a runner. The hub's projection is authoritative. */
export interface NextClaimWalk {
  readonly next: NextClaimCandidate | null;
  readonly passed: readonly NextClaimPassed[];
  readonly passed_total: number;
  readonly unserved: number;
  readonly if_holding: NextClaimCandidate | null;
  readonly held_at: readonly NextClaimPassed[];
}

/** The walk without repository targets, and how many chunks ahead it never offers at them. */
export interface NextClaimScenario extends NextClaimWalk {
  readonly excluded: number;
}

/** The walk at repository targets, with the no-target alternative where this hub reports one. */
export interface NextClaim extends NextClaimWalk {
  readonly without_repository_targets: NextClaimScenario | null;
}

/** A prerequisite chunk as the walk's display reads it: its id and lane status. */
export interface PrerequisiteStatus {
  readonly chunk_id: string;
  readonly laneStatus: LaneStatus;
}

/** A pass reason as rendered — a known reason as itself, an unknown one as the raw word. */
export function passReasonWord(reason: ClaimPassReason): string {
  return typeof reason === 'string' ? reason : reason.raw;
}

/** A lane status as the walk's display words it — the status itself, or the raw word for one this build cannot name. */
export function laneStatusWord(status: LaneStatus): string {
  return status.kind === 'unknown' ? status.raw : status.kind;
}
