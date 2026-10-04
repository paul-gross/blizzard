import { ageMs, formatAge, type runnerApi } from 'fleet';

import type { SubscriptionRow } from './app-subscriptions-view';

/** Operator-facing text per closed-set miss reason — what to do about it, not the machine word. */
export const MISS_REASON_TEXT: Readonly<Record<runnerApi.SampleMissReason, string>> = {
  credential_lapsed: 'credential lapsed: log in again',
  credential_unreadable: 'credential unreadable',
  endpoint_unreachable: 'endpoint unreachable',
  response_unparseable: 'response unparseable',
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
    renewalLabel: sub.renewal ?? null,
    ok: sub.ok ?? null,
  }));
}
