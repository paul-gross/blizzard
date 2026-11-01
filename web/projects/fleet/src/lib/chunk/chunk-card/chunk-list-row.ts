import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import { shortCommitHash } from '../../core/commit-hash';
import { FleetWhen } from '../../core/when-display';
import { KitBadge, KitSelectRow, KitSkeleton, projectTint, toneColor } from '../../kit';
import { type ChunkCardView, foldTitles } from './chunk-card.model';

/**
 * One chunk as a row of a board's list view, read across the full width: its id over its work items and, once
 * finished, when it finished; the runner holding it over its step and delivery; then its cost, or else the status
 * its row names, in the status tone.
 *
 * The row lays its cells on its container's `--list-columns` tracks, so each board sets its own columns. The left
 * edge, the id and the closing line are drawn in the status tone (`toneColor`, through one bound `--edge` property).
 * What only one app draws enters through named slots: a lead ahead of the work (`[chunkRowLead]`), the closing
 * line's icon (`[chunkRowFinishedIcon]`), and a live worker's line under the step (`[chunkRowWorker]`).
 *
 * Presentational only: activation leaves as {@link selectChunk}, exactly as a card's does.
 */
@Component({
  selector: 'fleet-chunk-list-row',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitBadge, KitSelectRow, KitSkeleton],
  templateUrl: './chunk-list-row.html',
  styleUrl: './chunk-list-row.css',
})
export class ChunkListRow {
  protected readonly shortCommitHash = shortCommitHash;
  protected readonly toneColor = toneColor;

  readonly card = input.required<ChunkCardView>();
  readonly selected = input(false);
  readonly gated = input(false);

  /** Emitted with the chunk id when the row is activated. */
  readonly selectChunk = output<string>();

  protected readonly expanded = signal(false);

  protected readonly projectTint = computed(() => projectTint(this.card().projectColor));

  /** The items the row shows — each a work-ref label over its title — and how many its fold hides. */
  protected readonly fold = computed(() => foldTitles(this.card().workLines, this.expanded()));

  /** The status tone as a colour — the row's left edge, id and closing line. */
  protected readonly edge = computed(() => toneColor(this.card().tone));

  protected toggleFold(): void {
    this.expanded.update((open) => !open);
  }
}
