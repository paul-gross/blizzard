import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import { FleetWhen } from '../../core/when-display';
import { KitBadge, KitButton, KitSelectRow, KitSkeleton, projectTint, toneColor } from '../../kit';
import { type ChunkCardView, foldTitles } from './chunk-card.model';

/**
 * One chunk as a board card, for every lane of either app's board. `[attr.data-chunk]` carries the full chunk id, a
 * unique locator, since same-instant chunk ids share a 12-char prefix.
 *
 * The edge and the id are drawn in the chunk's status tone (`toneColor`, through one bound `--edge` property); a
 * selected card's edge yields to the selection cyan. What only one app draws enters through a named slot — a live
 * worker's line under the titles (`[chunkCardWorker]`) — so the card never reaches into either app.
 *
 * Presentational only (`bzh:frontend-container-presentational`): every output carries the chunk id.
 */
@Component({
  selector: 'fleet-chunk-card',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitBadge, KitButton, KitSelectRow, KitSkeleton],
  templateUrl: './chunk-card.html',
  styleUrl: './chunk-card.css',
})
export class ChunkCard {
  protected readonly toneColor = toneColor;

  readonly card = input.required<ChunkCardView>();

  /** Whether this card is the current selection; it then carries the selection highlight. */
  readonly selected = input(false);

  /** Whether the chunk has an open decision awaiting a person — the card then carries the `gate` marker. */
  readonly gated = input(false);

  /** Whether the current identity may promote a chunk — withholds Promote when `false`. */
  readonly canControl = input(false);

  /** Whether the step row names the runner holding the chunk ahead of its step. */
  readonly showHolder = input(false);

  /** Emitted with the chunk id when the card is activated. */
  readonly selectChunk = output<string>();

  /** Emitted with the chunk id when a promotable card's Promote is clicked. */
  readonly promote = output<string>();

  protected readonly projectTint = computed(() => projectTint(this.card().projectColor));

  /** The status tone as a colour — the card's edge and id. */
  protected readonly edge = computed(() => toneColor(this.card().tone));

  protected readonly expanded = signal(false);

  /** The items the card shows, and how many its fold hides. */
  protected readonly fold = computed(() => foldTitles(this.card().workLines, this.expanded()));

  /** The step row's hover text: the holder ahead of the step's own, where the row names the holder. */
  protected readonly stepTitle = computed(() => {
    const { holder, stepTitle } = this.card();
    return this.showHolder() && holder ? `${holder.title} · ${stepTitle ?? ''}` : stepTitle;
  });

  protected toggleFold(): void {
    this.expanded.update((open) => !open);
  }

  /** Opens the chunk on a click anywhere on the card but a control of its own — a link or a button other than the
   * open control beneath the rows — which acts for itself. */
  protected open(event: MouseEvent): void {
    const control = (event.target as Element).closest('a, button');
    if (control && !control.closest('.card-open')) return;
    this.selectChunk.emit(this.card().chunkId);
  }
}
