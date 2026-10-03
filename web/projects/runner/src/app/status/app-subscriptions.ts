import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { ageMs, asyncState, formatAge, injectNowSignal, KitAsyncState } from 'fleet';

import { type SubscriptionRow, LocalSubscriptionsView } from './app-subscriptions-view';
import { injectRunnerDashboardQuery } from './status.query';

/** Operator-facing text per closed-set miss reason — what to do about it, not the machine word. */
const MISS_REASON_TEXT: Readonly<Record<string, string>> = {
  credential_lapsed: 'credential lapsed: log in again',
  credential_unreadable: 'credential unreadable',
  endpoint_unreachable: 'endpoint unreachable',
  response_unparseable: 'response unparseable',
};

/**
 * The subscriptions panel **container** — every declared provider subscription's own
 * newest sampling attempt: whether it sampled successfully, and when not,
 * the closed-set reason distinguishing a lapsed credential ("log in again") from an
 * unreachable endpoint or an unparseable response. Owns the shared dashboard query's
 * `subscriptions` section, the resolved async-state triad, and the ticking clock
 * {@link SubscriptionRow.sampledAgo} is derived from; the presentational
 * {@link LocalSubscriptionsView} owns the row template (`bzh:frontend-container-presentational`).
 * Read-only, like every other rail on this panel — renewal is driven by
 * the runner's own loop, never by an operator action here; this rail only shows its
 * newest recorded outcome.
 */
@Component({
  selector: 'app-subscriptions',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, LocalSubscriptionsView],
  templateUrl: './app-subscriptions.html',
  styleUrl: './app-subscriptions.css',
})
export class LocalSubscriptions {
  protected readonly query = injectRunnerDashboardQuery();

  protected readonly subscriptions = computed(() => this.query.data()?.subscriptions?.items ?? []);

  /** The async triad's resolved state — loading/error take precedence, then
   * no declared subscriptions, else the rows render. */
  protected readonly triadState = computed(() => asyncState(this.query, this.subscriptions().length === 0));

  /** Ticks once a second so each row's `sampledAgo` advances between polls instead of
   * sitting frozen at whatever age the last read carried. */
  private readonly now = injectNowSignal(1000);

  private sampledAgo(sampledAt: string | null): string {
    const age = ageMs(sampledAt, this.now());
    return age === null ? 'never' : `${formatAge(age)} ago`;
  }

  /** "ok", "never sampled", or "miss: <operator text>" — the operator-facing distinguishable
   * condition. The strings are a hand-kept copy of the runner CLI's `MISS_REASON_TEXT`; nothing
   * keeps the two in sync, so change both together. */
  private conditionLabel(ok: boolean | null, missReason: string | null): string {
    if (ok === null) return 'never sampled';
    if (ok) return 'ok';
    return `miss: ${missReason === null ? 'unknown' : (MISS_REASON_TEXT[missReason] ?? missReason)}`;
  }

  protected readonly rows = computed<readonly SubscriptionRow[]>(() =>
    this.subscriptions().map((sub) => ({
      slug: sub.slug,
      name: sub.name,
      provider: sub.provider,
      conditionLabel: this.conditionLabel(sub.ok ?? null, sub.miss_reason ?? null),
      sampledAgo: this.sampledAgo(sub.sampled_at ?? null),
      renewalLabel: sub.renewal ?? null,
      ok: sub.ok ?? null,
    })),
  );
}
