import { ChangeDetectionStrategy, Component } from '@angular/core';

import { KitSkeleton } from 'fleet';

/**
 * A list row's loading stand-in, on the list's own column tracks and at a one-item row's height:
 * the lane icon's disc, the id over a work ref over a title, then the step, the runner, and the
 * cost.
 *
 * Presentational, input-free.
 */
@Component({
  selector: 'app-board-list-row-skeleton',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitSkeleton],
  templateUrl: './board-list-row-skeleton.html',
  styleUrl: './board-list-row-skeleton.css',
  host: { 'aria-hidden': 'true', 'data-testid': 'list-row-skeleton' },
})
export class BoardListRowSkeleton {}
