import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { KitBadge } from '../kit/kit-badge';

/**
 * The runner registry's gate render — "Gates:" followed by one badge per node name the
 * runner holds for a human decision. A runner imposing none renders nothing: the row's
 * absence is the statement, unlike capabilities, where an empty report is itself news.
 *
 * Presentational only.
 */
@Component({
  selector: 'fleet-gate-badge-group',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge],
  templateUrl: './gate-badge-group.html',
  styleUrl: './gate-badge-group.css',
})
export class GateBadgeGroup {
  /** The node names this runner gates, in the order it declared them. */
  readonly gates = input.required<readonly string[]>();
}
