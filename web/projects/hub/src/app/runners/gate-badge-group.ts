import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { KitBadge, type UnenforceableGateView } from 'fleet';

/**
 * The runner registry's gate render — "Gates:" followed by one badge per node name the
 * runner holds for a human decision. A runner imposing none renders nothing: the row's
 * absence is the statement, unlike capabilities, where an empty report is itself news.
 * A gate the hub reports as not enforceable carries a "not enforceable" marker with its
 * reason beside the name, visible in the badge rather than only in a tooltip.
 *
 * Presentational only.
 */
@Component({
  selector: 'app-gate-badge-group',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge],
  templateUrl: './gate-badge-group.html',
  styleUrl: './gate-badge-group.css',
})
export class GateBadgeGroup {
  /** The node names this runner gates, in the order it declared them. */
  readonly gates = input.required<readonly string[]>();

  /** The gates the hub reports as not enforceable, each with its reason; empty when every gate can park a chunk. */
  readonly unenforceable = input<readonly UnenforceableGateView[]>([]);

  /** The reason `gate` is not enforceable, or `null` when it is — shown raw when unrecognized. */
  protected reasonOf(gate: string): string | null {
    return this.unenforceable().find((flag) => flag.node === gate)?.reason ?? null;
  }

  /** Whether the list carries its own "Gates:" label — off where a surrounding fact grid
   * already labels it. */
  readonly labelled = input(true);
}
