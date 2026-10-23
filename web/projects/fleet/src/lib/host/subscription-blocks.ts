import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { KitPaceBar } from '../kit/kit-pace-bar';
import type { SubscriptionLine } from './subscription-blocks.model';

/**
 * One subscription at a glance: its name over its last reading's age, then a
 * line of blocks per rate-limit window, each a `KitPaceBar` in its `blocks` variant. A lapsed credential draws a cross in place of
 * the blocks, a subscription with no window to draw its note or else a dash; a stale reading dims the whole line.
 *
 * Presentational only.
 */
@Component({
  selector: 'fleet-subscription-blocks',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './subscription-blocks.html',
  imports: [KitPaceBar],
  styleUrl: './subscription-blocks.css',
  host: { '[class.stale]': 'line().stale', '[attr.data-subscription-slug]': 'line().slug' },
})
export class SubscriptionBlocks {
  readonly line = input.required<SubscriptionLine>();
}
