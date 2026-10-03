import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/** One resolved row for {@link LocalAsksView} — every value is already resolved for
 * display (including {@link askedFor}'s clock-driven text), so the view itself
 * injects nothing. */
export interface AskRow {
  readonly questionId: string;
  readonly chunkRef: string;
  readonly askedFor: string;
  readonly question: string;
}

/**
 * {@link LocalAsks}'s presentational sibling (`bzh:frontend-container-presentational`):
 * plain inputs only, injects nothing, and owns the row template — the container
 * keeps the query, the resolved async-state triad, and the ticking clock
 * {@link AskRow.askedFor} is derived from.
 */
@Component({
  selector: 'app-asks-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './app-asks-view.html',
  styleUrl: './app-asks-view.css',
})
export class LocalAsksView {
  readonly rows = input<readonly AskRow[]>([]);
}
