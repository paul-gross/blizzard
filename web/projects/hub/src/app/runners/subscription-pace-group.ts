import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { KitPaceBar } from 'fleet';
import type { SubscriptionPace } from './runner-rows';

/**
 * The runner registry's per-subscription pace render — one group per
 * declared subscription, headed by its operator-facing name and keyed by slug, so two
 * subscriptions reporting identically labelled windows (both a `"5h"`) never merge into
 * one bar list. A sample with no folded {@link SubscriptionPace.paceBars} renders no
 * fabricated zero-utilization bar.
 *
 * Render precedence per group, in order: a lapsed credential shows
 * its notice in place of the bars, even over a surviving last-good sample; otherwise a
 * sample shows its bars, or "no usage windows" for one with zero; otherwise the group
 * reads "no sample yet", naming the newest miss reason when there is one. The refreshed
 * label and its age tier render whenever a last good sample exists, including under the
 * lapsed notice.
 *
 * Presentational only.
 */
@Component({
  selector: 'app-subscription-pace-group',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitPaceBar],
  templateUrl: './subscription-pace-group.html',
  styleUrl: './subscription-pace-group.css',
})
export class SubscriptionPaceGroup {
  /** Every reported subscription, each pre-folded to its own pace bars. */
  readonly subscriptionPaces = input.required<readonly SubscriptionPace[]>();
}
