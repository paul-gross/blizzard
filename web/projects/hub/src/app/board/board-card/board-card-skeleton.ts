import { ChangeDetectionStrategy, Component } from '@angular/core';

import { KitSkeleton } from 'fleet';

/**
 * A board card's loading stand-in, shaped and sized like a one-item card: a work-ref line, a
 * two-line title, then the step row with the id at its end — so a lane filling in keeps its
 * height rather than jumping.
 *
 * Presentational, input-free.
 */
@Component({
  selector: 'app-board-card-skeleton',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitSkeleton],
  templateUrl: './board-card-skeleton.html',
  styleUrl: './board-card-skeleton.css',
  host: { 'aria-hidden': 'true', 'data-testid': 'chunk-card-skeleton' },
})
export class BoardCardSkeleton {}
