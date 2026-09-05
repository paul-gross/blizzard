import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { KitBeacon } from 'fleet';

/** One resolved row for {@link EnvListView} — every display value the container
 * derives (including {@link heldFor}'s clock-driven text), so the view itself
 * injects nothing. */
export interface EnvRow {
  readonly environmentId: string;
  readonly isHeld: boolean;
  readonly chunkRef: string;
  readonly heldFor: string;
}

/**
 * {@link EnvList}'s presentational sibling (`bzh:frontend-container-presentational`):
 * plain inputs only, injects nothing, and owns the row template — the container
 * keeps the query, the resolved async-state triad, and the ticking clock
 * {@link EnvRow.heldFor} is derived from.
 */
@Component({
  selector: 'local-env-list-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBeacon],
  templateUrl: './env-list-view.html',
  styleUrl: './env-list-view.css',
})
export class EnvListView {
  readonly rows = input<readonly EnvRow[]>([]);
}
