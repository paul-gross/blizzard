import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { ageMs, asyncState, formatAge, injectNowSignal, KitAsyncState } from 'fleet';

import { type SubscriptionRow, LocalSubscriptionsView } from './local-subscriptions-view';
import { injectRunnerDashboardQuery } from './status.query';

/**
 * The subscriptions panel **container** — every declared provider subscription's own
 * newest sampling attempt (blizzard#504): whether it sampled successfully, and when not,
 * the closed-set reason distinguishing a lapsed credential ("log in again") from an
 * unreachable endpoint or an unparseable response. Owns the shared dashboard query's
 * `subscriptions` section, the resolved async-state triad, and the ticking clock
 * {@link SubscriptionRow.sampledAgo} is derived from; the presentational
 * {@link LocalSubscriptionsView} owns the row template (`bzh:frontend-container-presentational`).
 * Read-only, like every other rail on this panel — renewal (blizzard#504) is driven by
 * the runner's own loop, never by an operator action here; this rail only shows its
 * newest recorded outcome.
 */
@Component({
  selector: 'local-subscriptions',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, LocalSubscriptionsView],
  templateUrl: './local-subscriptions.html',
  styleUrl: './local-subscriptions.css',
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

  /** "ok", "never sampled", or "miss: <reason>" — the operator-facing distinguishable
   * condition (blizzard#504), mirroring the probe CLI's own translation. */
  private conditionLabel(ok: boolean | null, missReason: string | null): string {
    if (ok === null) return 'never sampled';
    if (ok) return 'ok';
    return `miss: ${missReason ?? 'unknown'}`;
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
