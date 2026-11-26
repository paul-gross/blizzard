import type { UnknownHostValue } from 'fleet/core';

export type KnownLeaseState = 'running' | 'stale' | 'parked' | 'backing-off' | 'spawning' | 'exited' | 'closed';
/** A lease's read-time-derived state; `UnknownHostValue` carries one this build cannot name. */
export type LeaseState = KnownLeaseState | UnknownHostValue;

export type KnownLeaseDisplayStatus = 'human-in-session' | 'needs-human' | 'waiting-ask' | KnownLeaseState;
/** What a lease displays as — an open takeover, escalation, or ask outranks its own state. */
export type LeaseDisplayStatus = KnownLeaseDisplayStatus | UnknownHostValue;

/** Whether a lease's worker is still part of the fleet's work; `unknown` is a reading this build cannot name. */
export type LeaseLiveness = 'live' | 'ended' | 'unknown';

export type KnownLeaseClosureReason = 'transitioned' | 'reaped' | 'failed' | 'escalated' | 'parked' | 'released' | 'preempted';
export type LeaseClosureReason = KnownLeaseClosureReason | UnknownHostValue;

/** One commit a lease's worker declared. */
export interface LeaseGitCommit {
  readonly branch: string;
  readonly commit: string;
  readonly declared_at: string;
  readonly environment_id: string;
  readonly repo: string;
}

/** One lease — active or recently closed — with its joined binding facts and the runner's read-time judgments. */
export interface Lease {
  readonly lease_id: string;
  readonly chunk_id: string;
  readonly graph_id: string;
  readonly node_id: string;
  readonly node_name: string;
  readonly epoch: number;
  readonly session_id: string | null;
  readonly pid: number | null;
  readonly environment_id: string | null;
  readonly workdir: string | null;
  readonly project_id: string | null;
  readonly harness_id: string | null;
  readonly harness_version: string | null;
  readonly resolved_effort: string | null;
  readonly resolved_model: string | null;
  readonly host_settings_revision: number | null;
  readonly runner_settings_revision: number | null;
  readonly git_commits: readonly LeaseGitCommit[];
  readonly created_at: string;
  readonly last_heartbeat_at: string | null;
  readonly stale_after_seconds: number;
  readonly closed_at: string | null;
  readonly closure_healthy: boolean;
  readonly closure_reason: LeaseClosureReason | null;
  readonly display_status: LeaseDisplayStatus;
  readonly state: LeaseState;
  readonly liveness: LeaseLiveness;
}

