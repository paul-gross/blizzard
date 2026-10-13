import type * as runnerApi from '../api/runner';
import { compactRef } from '../core/compact-ref';
import { ageMs, formatAge, formatHeldFor } from '../core/when';
import type { SubscriptionPace } from './subscription-pace.model';
import type { EnvironmentRow, WorkspaceRow } from './workspace-table';

/**
 * `42m` since the binding fact — browser-clock decoration only
 * (`bzh:utc-instants` via `ageMs`): a skew-broken timestamp renders `—`, and an
 * unheld environment (no `held_since`) renders blank rather than `—`.
 */
export function heldForLabel(heldSince: string | null | undefined, now: number): string {
  if (heldSince == null) return '';
  const age = ageMs(heldSince, now);
  return age === null ? '—' : formatHeldFor(age);
}

/** One {@link EnvironmentRow} per pool environment, in wire order, aged against `now`. */
export function runnerEnvironmentRows(envs: readonly runnerApi.EnvironmentView[], now: number): readonly EnvironmentRow[] {
  return envs.map((env) => ({
    environmentId: env.environment_id,
    isHeld: env.chunk_id != null,
    chunkRef: env.chunk_id == null ? '' : compactRef(env.chunk_id),
    heldFor: heldForLabel(env.held_since, now),
    holder: null,
  }));
}

/** The runner dashboard's pool as the shared workspace table's one workspace — the
 * dashboard names no workspace, so the pool renders as its environments alone. */
export function runnerWorkspaceRows(envs: readonly runnerApi.EnvironmentView[], now: number): readonly WorkspaceRow[] {
  const environments = runnerEnvironmentRows(envs, now);
  return [
    {
      workspaceId: null,
      capacity: environments.length,
      inUse: environments.filter((env) => env.isHeld).length,
      flag: null,
      environments,
    },
  ];
}

/** Operator-facing text per closed-set miss reason — what to do about it, not the machine word. */
export const MISS_REASON_TEXT: Readonly<Record<runnerApi.SampleMissReason, string>> = {
  credential_lapsed: 'credential lapsed: log in again',
  credential_unreadable: 'credential unreadable',
  endpoint_unreachable: 'endpoint unreachable',
  response_unparseable: 'response unparseable',
};

/** Operator-facing text per closed-set renewal failure reason. */
export const RENEWAL_FAILURE_TEXT: Readonly<Record<runnerApi.RenewalFailureReason, string>> = {
  renewer_unavailable: 'vendor CLI unavailable',
  timed_out: 'timed out',
  vendor_refused: 'vendor refused',
  protocol_error: 'unreadable vendor response',
};

/** `-34s ago` since the newest sampling attempt, or `never` when there is none or it is skew-broken. */
export function sampledAgoLabel(sampledAt: string | null, now: number): string {
  const age = ageMs(sampledAt, now);
  return age === null ? 'never' : `${formatAge(age)} ago`;
}

/** "ok", "never sampled", or "miss: <operator text>" — the operator-facing distinguishable
 * condition, one string per wire `SampleMissReason`. */
export function conditionLabel(ok: boolean | null, missReason: runnerApi.SampleMissReason | null): string {
  if (ok === null) return 'never sampled';
  if (ok) return 'ok';
  return `miss: ${missReason === null ? 'unknown' : MISS_REASON_TEXT[missReason]}`;
}

/** The newest credential renewal in operator words, aged against `now` — `null` when the
 * subscription was never renewed. Reads the wire's typed result and reason; parses nothing. */
export function renewalLabel(
  result: runnerApi.RenewalResult | null,
  failureReason: runnerApi.RenewalFailureReason | null,
  attemptedAt: string | null,
  now: number,
): string | null {
  if (result === null) return null;
  const ago = sampledAgoLabel(attemptedAt, now);
  switch (result) {
    case 'renewed':
      return `renewed ${ago}`;
    case 'failed':
      return `failed (${failureReason === null ? 'unknown cause' : RENEWAL_FAILURE_TEXT[failureReason]}) ${ago}`;
    case 'unrecorded':
      return `attempted ${ago}, outcome not recorded`;
  }
}

/** One shared subscription row per declared subscription, in wire order, aged against
 * `now`. The dashboard reports the newest sampling attempt rather than usage windows, so
 * each row carries its {@link SubscriptionPace.sample} line and no pace bars. */
export function runnerSubscriptionPaces(
  subs: readonly runnerApi.SubscriptionView[],
  now: number,
): readonly SubscriptionPace[] {
  return subs.map((sub) => ({
    slug: sub.slug,
    name: sub.name,
    provider: sub.provider,
    paceBars: [],
    condition: null,
    sampledAt: null,
    refreshedLabel: null,
    freshness: null,
    missReason: null,
    sample: {
      conditionLabel: conditionLabel(sub.ok ?? null, sub.miss_reason ?? null),
      sampledAgo: sampledAgoLabel(sub.sampled_at ?? null, now),
      ok: sub.ok ?? null,
    },
    renewalLabel: renewalLabel(
      sub.renewal_result ?? null,
      sub.renewal_failure_reason ?? null,
      sub.renewal_attempted_at ?? null,
      now,
    ),
  }));
}
