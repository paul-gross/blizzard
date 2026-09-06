import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { KitPaceBar } from '../kit/kit-pace-bar';
import type { SubscriptionPace } from './runner-panel';

/**
 * The runner registry's per-subscription pace render (blizzard#478) — one group per
 * declared subscription, headed by its operator-facing name and keyed by slug, so two
 * subscriptions reporting identically labelled windows (both a `"5h"`) never merge into
 * one bar list. A subscription with no folded {@link SubscriptionPace.paceBars} reads
 * as **not yet sampled**, never as a fabricated zero-utilization bar — the same
 * never-fabricate rule the legacy single-subscription pace bars already hold to
 * (issue #218).
 *
 * Presentational only, split out of {@link RunnerPanelView}'s markup rather than folded
 * into its existing flat window loop: that loop stays the legacy single-subscription
 * fallback, this owns the per-slug grouped render exclusively.
 */
@Component({
  selector: 'fleet-subscription-pace-group',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitPaceBar],
  templateUrl: './subscription-pace-group.html',
  styleUrl: './subscription-pace-group.css',
})
export class SubscriptionPaceGroup {
  /** Every subscription this runner has declared, each pre-folded to its own pace
   * bars (empty when declared but not yet sampled). */
  readonly subscriptionPaces = input.required<readonly SubscriptionPace[]>();
}
