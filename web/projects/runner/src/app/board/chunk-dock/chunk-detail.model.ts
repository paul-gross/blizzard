import { ageMs, compactRef, type DaemonTarget, formatAge, runnerClient, type runnerApi } from 'fleet';

import type { PanelLease } from '../../core/panel-reads.model';
import { isLiveLease } from '../../core/worker-session';

/** The detail read's target — the runner `runnerId` names, or `null` while no chunk is selected. */
export function runnerTarget(runnerId: string | null): DaemonTarget | null {
  return runnerId === null ? null : { plane: 'runner', client: runnerClient, runnerId };
}

/** The chunk's live worker — its newest lease while that has not ended, else `null`. */
export function liveWorker(newest: PanelLease | null): PanelLease | null {
  return newest && isLiveLease(newest) ? newest : null;
}

/** The lease's compact ref, or blank when nothing is selected. */
export function leaseRefLabel(lease: runnerApi.LeaseView | null): string {
  return lease ? compactRef(lease.lease_id) : '';
}

/**
 * `-34s` shorthand, or `—` before the first beat / past the skew bound / once the
 * lease is closed — decoration only; the server-derived state carries liveness
 * (`bzh:utc-instants`).
 */
export function heartbeatLabel(lease: runnerApi.LeaseView | null, now: number): string {
  if (!lease || lease.state === 'closed') return '—';
  const age = ageMs(lease.last_heartbeat_at, now);
  return age === null ? '—' : formatAge(age);
}

/** One requeue's outcome on its chunk: it landed, or it failed with `message`. */
export type RequeueFeedback = { readonly kind: 'requeued' } | { readonly kind: 'error'; readonly message: string };

/** Requeue outcomes by {@link requeueKey}. */
export type RequeueFeedbackMap = ReadonlyMap<string, RequeueFeedback>;

/** The key a requeue's outcome is held under — its runner and its chunk. */
export function requeueKey(runnerId: string, chunkId: string): string {
  return `${runnerId}\u0000${chunkId}`;
}

/** The outcome held for `chunkId` on `runnerId`, or `null` while there is none or nothing is selected. */
export function requeueFeedbackFor(
  feedback: RequeueFeedbackMap,
  runnerId: string | null,
  chunkId: string | null,
): RequeueFeedback | null {
  if (runnerId === null || chunkId === null) return null;
  return feedback.get(requeueKey(runnerId, chunkId)) ?? null;
}

/** `feedback` with the entry for `chunkId` on `runnerId` set to `entry`, or removed when `entry` is `null`. */
export function withRequeueFeedback(
  feedback: RequeueFeedbackMap,
  runnerId: string,
  chunkId: string,
  entry: RequeueFeedback | null,
): RequeueFeedbackMap {
  const next = new Map(feedback);
  if (entry === null) next.delete(requeueKey(runnerId, chunkId));
  else next.set(requeueKey(runnerId, chunkId), entry);
  return next;
}

/** A requeue outcome's failure message, or `null` when it landed or there is none. */
export function requeueErrorOf(feedback: RequeueFeedback | null): string | null {
  return feedback?.kind === 'error' ? feedback.message : null;
}
