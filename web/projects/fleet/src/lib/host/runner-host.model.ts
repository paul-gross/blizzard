import type { RunnerSubscription, SampleMiss, KnownSampleMiss, RenewalResult, RenewalFailure, KnownRenewalFailure } from '../core/host.domain';
import { openLookup } from '../core/open-lookup';
import { ageMs, formatAge, formatRefreshedAgo, formatSeenTag } from '../core/when';
import { type SubscriptionLine, subscriptionLine } from './subscription-blocks.model';
import { classifySubscriptionFreshness, type SubscriptionPace, toPaceBars } from './subscription-pace.model';

/** Operator-facing text per miss reason — what to do about it, not the machine word. A reason
 * this build does not know reads raw. */
export const MISS_REASON_TEXT = openLookup<KnownSampleMiss, string>(
  {
    credential_lapsed: 'credential lapsed: log in again',
    credential_unreadable: 'credential unreadable',
    endpoint_unreachable: 'endpoint unreachable',
    response_unparseable: 'response unparseable',
  },
  (raw) => raw,
);

/** Operator-facing text per renewal failure reason; a reason this build does not know reads raw. */
export const RENEWAL_FAILURE_TEXT = openLookup<KnownRenewalFailure, string>(
  {
    renewer_unavailable: 'vendor CLI unavailable',
    timed_out: 'timed out',
    vendor_refused: 'vendor refused',
    protocol_error: 'unreadable vendor response',
  },
  (raw) => raw,
);

/** `-34s ago` since the newest sampling attempt, or `never` when there is none or it is skew-broken. */
export function sampledAgoLabel(sampledAt: string | null, now: number): string {
  const age = ageMs(sampledAt, now);
  return age === null ? 'never' : `${formatAge(age)} ago`;
}

/** "ok", "never sampled", or "miss: <operator text>" — the operator-facing distinguishable
 * condition, one string per wire `SampleMissReason`. */
export function conditionLabel(ok: boolean | null, missReason: SampleMiss | null): string {
  if (ok === null) return 'never sampled';
  if (ok) return 'ok';
  return `miss: ${missReason === null ? 'unknown' : typeof missReason === 'string' ? MISS_REASON_TEXT(missReason) : missReason.raw}`;
}

/** The newest credential renewal in operator words, aged against `now` — `null` when the
 * subscription was never renewed. Reads the wire's typed result and reason; parses nothing. */
export function renewalLabel(
  result: RenewalResult | null,
  failureReason: RenewalFailure | null,
  attemptedAt: string | null,
  now: number,
): string | null {
  if (result === null) return null;
  const ago = sampledAgoLabel(attemptedAt, now);
  switch (result) {
    case 'renewed':
      return `renewed ${ago}`;
    case 'failed':
      return `failed (${failureReason === null ? 'unknown cause' : typeof failureReason === 'string' ? RENEWAL_FAILURE_TEXT(failureReason) : failureReason.raw}) ${ago}`;
    case 'unrecorded':
      return `attempted ${ago}, outcome not recorded`;
    default:
      return `${result.raw} ${ago}`;
  }
}

/** One shared subscription row per declared subscription, in wire order, aged against
 * `now`: its newest sampled snapshot's windows as pace bars, refreshed against that snapshot,
 * naming the runners spending it — `runnerName` resolves a spender's id to its display name.
 * A lapsed credential reads as lapsed; a subscription attempted but never sampled carries its
 * {@link SubscriptionPace.sample} line in place of the bars; one never attempted has no sample yet. */
export function runnerSubscriptionPaces(
  subs: readonly RunnerSubscription[],
  now: number,
  runnerName: (runnerId: string) => string,
): readonly SubscriptionPace[] {
  return subs.map((sub) => {
    const windowsSampledAt = sub.windows_sampled_at ?? null;
    const delta = windowsSampledAt === null ? null : ageMs(windowsSampledAt, now);
    const condition = sub.condition ?? null;
    const lapsed = condition === 'credential_lapsed';
    const ok = sub.ok ?? null;
    const missReason = sub.miss_reason ?? null;
    return {
      slug: sub.slug,
      name: sub.name,
      provider: sub.provider,
      paceBars: toPaceBars(now, sub.windows ?? []),
      condition: lapsed || (condition !== null && typeof condition !== 'string') ? condition : null,
      sampledAt: windowsSampledAt,
      refreshedLabel: delta === null ? null : formatRefreshedAgo(delta),
      refreshedTag: delta === null ? null : formatSeenTag(windowsSampledAt, now),
      freshness: delta === null ? null : classifySubscriptionFreshness(delta),
      missReason: ok === false ? missReason : null,
      sample:
        windowsSampledAt === null && !lapsed && ok !== null
          ? {
              conditionLabel: conditionLabel(ok, missReason),
              sampledAgo: sampledAgoLabel(sub.sampled_at ?? null, now),
              ok,
            }
          : null,
      spenders: (sub.spenders ?? []).map(runnerName),
      renewalLabel: renewalLabel(
        sub.renewal_result ?? null,
        sub.renewal_failure_reason ?? null,
        sub.renewal_attempted_at ?? null,
        now,
      ),
    };
  });
}

/** One line of blocks per declared subscription, in wire order, aged against `now`: its newest
 * sampled snapshot's windows, paced; a lapsed credential reads `lapsed`; one never sampled carries
 * its newest attempt's condition as the line's note, or the dash when never attempted. */
export function runnerSubscriptionLines(subs: readonly RunnerSubscription[], now: number): readonly SubscriptionLine[] {
  return runnerSubscriptionPaces(subs, now, (id) => id).map((pace) => ({
    ...subscriptionLine(pace),
    note: pace.sample?.conditionLabel ?? null,
  }));
}
