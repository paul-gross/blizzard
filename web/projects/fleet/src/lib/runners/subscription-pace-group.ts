import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { KitPaceBar } from '../kit/kit-pace-bar';
import type { SubscriptionPace } from './runner-rows';

/**
 * The runner registry's per-subscription pace render (blizzard#478) — one group per
 * reported subscription, headed by its operator-facing name and keyed by slug, so two
 * subscriptions reporting identically labelled windows (both a `"5h"`) never merge into
 * one bar list. A sample with no folded {@link SubscriptionPace.paceBars} renders no
 * fabricated zero-utilization bar.
 *
 * Presentational only.
 */
@Component({
  selector: 'fleet-subscription-pace-group',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitPaceBar],
  templateUrl: './subscription-pace-group.html',
  styleUrl: './subscription-pace-group.css',
})
export class SubscriptionPaceGroup {
  /** Every reported subscription, each pre-folded to its own pace bars. */
  readonly subscriptionPaces = input.required<readonly SubscriptionPace[]>();
}
