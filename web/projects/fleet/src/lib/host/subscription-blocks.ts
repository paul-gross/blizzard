import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { DENSE_AFTER } from '../kit/kit-slot-bar';
import type { SubscriptionLine } from './subscription-blocks.model';

/**
 * One subscription at a glance: its name over its last reading's age, then a
 * line of blocks per rate-limit window — filled to the window's usage, a notch where its clock
 * stands, red once usage runs ahead of that clock. A lapsed credential draws a cross in place of
 * the blocks, a subscription with no window to draw its note or else a dash; a stale reading dims the whole line. The
 * figures are each window's tooltip.
 *
 * Presentational only.
 */
@Component({
  selector: 'fleet-subscription-blocks',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './subscription-blocks.html',
  styleUrl: './subscription-blocks.css',
  host: { '[class.stale]': 'line().stale', '[attr.data-subscription-slug]': 'line().slug' },
})
export class SubscriptionBlocks {
  readonly line = input.required<SubscriptionLine>();

  protected readonly denseAfter = DENSE_AFTER;
}
