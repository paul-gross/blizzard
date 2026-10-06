import { ageMs, formatAge, type runnerApi } from 'fleet';

import type { SubscriptionRow } from './app-subscriptions-view';

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

/** One {@link SubscriptionRow} per declared subscription, in wire order, aged against `now`. */
export function subscriptionRows(
  subs: readonly runnerApi.SubscriptionView[],
  now: number,
): readonly SubscriptionRow[] {
  return subs.map((sub) => ({
    slug: sub.slug,
    name: sub.name,
    provider: sub.provider,
    conditionLabel: conditionLabel(sub.ok ?? null, sub.miss_reason ?? null),
    sampledAgo: sampledAgoLabel(sub.sampled_at ?? null, now),
    renewalLabel: renewalLabel(
      sub.renewal_result ?? null,
      sub.renewal_failure_reason ?? null,
      sub.renewal_attempted_at ?? null,
      now,
    ),
    ok: sub.ok ?? null,
  }));
}
