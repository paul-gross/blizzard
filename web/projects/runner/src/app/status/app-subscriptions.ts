import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { asyncState, injectNowSignal, KitAsyncState } from 'fleet';

import { type SubscriptionRow, LocalSubscriptionsView } from './app-subscriptions-view';
import { subscriptionRows } from './app-subscriptions.model';
import { injectRunnerDashboardQuery } from '../core/status.query';

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

  protected readonly rows = computed<readonly SubscriptionRow[]>(() =>
    subscriptionRows(this.subscriptions(), this.now()),
  );
}
