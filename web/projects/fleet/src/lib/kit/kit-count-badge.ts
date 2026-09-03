import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/**
 * A small numeric count badge — a waiting-count marker beside a tab label, a
 * pending-item count beside a list heading. Centers its digit(s) on both axes
 * (never a `line-height` proxy for vertical centering, which drifts the moment
 * either it or the font size changes independently), holds `font-variant-
 * numeric: tabular-nums` so a 9-to-10 transition never jitters the badge's own
 * width, and pairs `--amber-faint` behind `--snow` for a legible contrast
 * ratio rather than a washed-out one.
 */
@Component({
  selector: 'fleet-kit-count-badge',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-count-badge.html',
  styleUrl: './kit-count-badge.css',
})
export class KitCountBadge {
  readonly count = input.required<number>();
  readonly testid = input<string | null>(null);
}
