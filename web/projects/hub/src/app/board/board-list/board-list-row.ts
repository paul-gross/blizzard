import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import { formatCost, hasCostFigure, FleetWhen, KitBadge, KitSelectRow } from 'fleet';
import type { BoardCard } from '../board-card/board-card';
import { blockedLabel, foldTitles, stepLabel } from '../board-card/board-card.model';

/**
 * One row of the board's list view — a chunk read across the full width: its work items'
 * titles lead, and its project, step, runner, PRs, cost, and id sit in aligned columns.
 *
 * Presentational only: activation leaves as {@link selectChunk}, exactly as a board card's does.
 */
@Component({
  selector: 'app-board-list-row',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitBadge, KitSelectRow],
  templateUrl: './board-list-row.html',
  styleUrl: './board-list-row.css',
})
export class BoardListRow {
  protected readonly formatCost = formatCost;
  protected readonly hasCostFigure = hasCostFigure;

  readonly card = input.required<BoardCard>();
  readonly selected = input(false);
  readonly gated = input(false);

  /** Emitted with the chunk id when the row is activated. */
  readonly selectChunk = output<string>();

  protected readonly expanded = signal(false);

  /** The titles the row shows, and how many its fold hides. */
  protected readonly fold = computed(() => foldTitles(this.card().titles ?? [], this.expanded()));

  /** The heading while titles are unread, or when the chunk carries no work item. */
  protected readonly fallback = computed(() => this.card().pointerLabels[0] ?? this.card().shortId);

  protected readonly step = computed(() => stepLabel(this.card().status, this.card().node));

  protected readonly blocked = computed(() =>
    this.card().blockedOn ? blockedLabel(this.card().blockedOn, this.card().blockedCount) : null,
  );

  protected toggleFold(): void {
    this.expanded.update((open) => !open);
  }
}
