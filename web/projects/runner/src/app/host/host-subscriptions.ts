import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import {
  asyncState,
  injectNowSignal,
  KitAsyncState,
  runnerSubscriptionPaces,
  type SubscriptionPace,
  SubscriptionPaceGroup,
} from 'fleet';

import { injectHostSubscriptionsQuery, injectRunnerNames } from './host.query';

/**
 * The host's subscriptions **container** — every declared provider subscription's own
 * newest sampling attempt: whether it sampled successfully, and when not, the closed-set
 * reason distinguishing a lapsed credential ("log in again") from an unreachable endpoint
 * or an unparseable response — and the runners spending it. Owns the host's subscriptions
 * read, the resolved async-state triad, and the ticking clock each row's sampled-ago text
 * is derived from; the shared presentational {@link SubscriptionPaceGroup} owns the row
 * template (`bzh:frontend-container-presentational`). Read-only: it shows the newest
 * renewal and its outcome, and offers no operator action.
 */
@Component({
  selector: 'app-host-subscriptions',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, SubscriptionPaceGroup],
  templateUrl: './host-subscriptions.html',
  styleUrl: './host-subscriptions.css',
})
export class HostSubscriptions {
  protected readonly query = injectHostSubscriptionsQuery();
  private readonly runnerNames = injectRunnerNames();

  protected readonly subscriptions = computed(() => this.query.data() ?? []);

  /** The async triad's resolved state — loading/error take precedence, then
   * no declared subscriptions, else the rows render. */
  protected readonly triadState = computed(() => asyncState(this.query, this.subscriptions().length === 0));

  /** Ticks once a second so each row's `sampledAgo` advances between polls instead of
   * sitting frozen at whatever age the last read carried. */
  private readonly now = injectNowSignal();

  protected readonly rows = computed<readonly SubscriptionPace[]>(() =>
    runnerSubscriptionPaces(this.subscriptions(), this.now(), this.runnerNames()),
  );
}
