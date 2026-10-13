import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { SampleMissReason } from '../api/hub';
import { KitPaceBar } from '../kit/kit-pace-bar';
import type { SubscriptionPace } from './subscription-pace.model';

/**
 * The shared subscription list — one group per subscription, headed by its
 * operator-facing name (and provider, when the row carries one) and keyed by slug, so two
 * subscriptions reporting identically labelled windows (both a `"5h"`) never merge into
 * one bar list. A sample with no folded {@link SubscriptionPace.paceBars} renders no
 * fabricated zero-utilization bar.
 *
 * Render precedence per group, in order: a row carrying the daemon's own
 * {@link SubscriptionPace.sample} line shows its condition and sampled age; otherwise a
 * lapsed credential shows its notice in place of the bars, even over a surviving last-good
 * sample; otherwise a sample shows its bars, or "no usage windows" for one with zero;
 * otherwise the group reads "no sample yet", naming the newest miss reason when there is
 * one. The refreshed label and its age tier render whenever a last good sample exists,
 * including under the lapsed notice. The spender and renewal lines render when the row
 * carries them.
 *
 * Presentational only: each daemon's container folds its own read into the rows.
 */
@Component({
  selector: 'fleet-subscription-pace-group',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitPaceBar],
  templateUrl: './subscription-pace-group.html',
  styleUrl: './subscription-pace-group.css',
})
export class SubscriptionPaceGroup {
  protected readonly SampleMissReason = SampleMissReason;

  /** Every subscription, each pre-folded to its row. */
  readonly subscriptionPaces = input.required<readonly SubscriptionPace[]>();
}
