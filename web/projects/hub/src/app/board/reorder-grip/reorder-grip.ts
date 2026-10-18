import { ChangeDetectionStrategy, Component } from '@angular/core';

/**
 * The cue a draggable READY or BACKLOG entry carries — two columns of cyan dots, purely
 * decorative: the whole card or row stays the drag target. Its consumer positions it.
 *
 * Presentational, input-free.
 */
@Component({
  selector: 'app-reorder-grip',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './reorder-grip.html',
  styleUrl: './reorder-grip.css',
  host: { 'aria-hidden': 'true' },
})
export class ReorderGrip {}
