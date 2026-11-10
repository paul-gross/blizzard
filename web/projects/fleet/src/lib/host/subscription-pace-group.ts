import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { SampleMissReason } from '../api/hub';
import { KitPaceBar } from '../kit/kit-pace-bar';
import { toneColor } from '../kit/kit-badge';
import { freshnessTone, LAPSED_TONE, sampleTone, type SubscriptionPace } from './subscription-pace.model';

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

  /** The lapsed notice's colour — the one tone the blocks line reads too. */
  protected readonly lapsedColor = toneColor(LAPSED_TONE);

  /** The colour of a reading's age tier, `null` (inherited) for a fresh one. */
  protected freshnessColor(freshness: SubscriptionPace['freshness']): string | null {
    const tone = freshnessTone(freshness);
    return tone && toneColor(tone);
  }

  /** The colour of the daemon's sampling line, `null` (inherited) unless the attempt missed. */
  protected sampleColor(ok: boolean | null): string | null {
    const tone = sampleTone(ok);
    return tone && toneColor(tone);
  }

  /** Every subscription, each pre-folded to its row. */
  readonly subscriptionPaces = input.required<readonly SubscriptionPace[]>();
}
