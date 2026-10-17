import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { SubscriptionLine } from './runner-rail.model';

/**
 * One subscription on the board rail at a glance: its name over its last reading's age, then a
 * line of blocks per rate-limit window — filled to the window's usage, a notch where its clock
 * stands, red once usage runs ahead of that clock. A lapsed credential draws a cross in place of
 * the blocks, a subscription with no reading a dash; a stale reading dims the whole line. The
 * figures are each window's tooltip.
 *
 * Presentational only.
 */
@Component({
  selector: 'app-subscription-blocks',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './subscription-blocks.html',
  styleUrl: './subscription-blocks.css',
  host: { '[class.stale]': 'line().stale', '[attr.data-subscription-slug]': 'line().slug' },
})
export class SubscriptionBlocks {
  readonly line = input.required<SubscriptionLine>();
}
