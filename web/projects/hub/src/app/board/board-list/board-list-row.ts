import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import { formatCost, hasCostFigure, projectTint, runnerDisplayName, runnerTitle, shortCommitHash, FleetWhen, KitBadge, KitSelectRow, KitSkeleton, STATUS_TONE, toneColor } from 'fleet';
import { LaneIcon } from '../lane-icon/lane-icon';
import type { BoardCard } from '../board-card/board-card';
import { blockedLabel, finishedWord, foldTitles, stepLabel } from '../board-card/board-card.model';

/**
 * One row of the board's list view — a chunk read across the full width: its lane as an icon,
 * then its id over its work items (each a work-source link over its title) and, once finished,
 * when it finished; its step, its runner over its PRs, and its cost, in aligned columns.
 *
 * Presentational only: activation leaves as {@link selectChunk}, exactly as a board card's does.
 */
@Component({
  selector: 'app-board-list-row',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitBadge, KitSelectRow, KitSkeleton, LaneIcon],
  templateUrl: './board-list-row.html',
  styleUrl: './board-list-row.css',
})
export class BoardListRow {
  protected readonly formatCost = formatCost;
  protected readonly hasCostFigure = hasCostFigure;
  protected readonly finishedWord = finishedWord;
  protected readonly shortCommitHash = shortCommitHash;

  readonly card = input.required<BoardCard>();
  /** The board lane the chunk sits in, drawn as the row's leading icon. */
  readonly lane = input.required<string>();
  readonly selected = input(false);
  readonly gated = input(false);

  /** Emitted with the chunk id when the row is activated. */
  readonly selectChunk = output<string>();

  protected readonly expanded = signal(false);

  /** The chunk's project color the row's background is tinted with, or `null` for an untinted row. */
  protected readonly projectTint = computed(() => projectTint(this.card().projectColor));

  /** Whether the chunk's work items are still unread — each title then holds a skeleton. */
  protected readonly titlesPending = computed(() => !this.card().itemsRead);

  /** The items the row shows — each a work-ref label over its title — and how many its fold hides. */
  protected readonly fold = computed(() => foldTitles(this.card().workLines, this.expanded()));

  /** The runner holding the chunk, by its display name and its tooltip, or `null` when none holds it. */
  protected readonly holder = computed(() => {
    const { runnerId, runnerName, hostName } = this.card();
    return runnerId ? { name: runnerDisplayName(runnerId, runnerName, hostName), title: runnerTitle(runnerId, runnerName, hostName) } : null;
  });

  protected readonly step = computed(() => stepLabel(this.card().status, this.card().node));

  protected readonly blocked = computed(() =>
    this.card().blockedOn ? blockedLabel(this.card().blockedOn, this.card().blockedCount) : null,
  );

  /** When a done-lane chunk finished, or `null` — the row's closing line. */
  protected readonly finishedAt = computed(() => (this.card().terminal ? this.card().completedAt : null));

  /** The closing line's color: the chunk's status tone, as its lane icon takes. */
  protected readonly finishedColor = computed(() => toneColor(STATUS_TONE(this.card().status)));

  protected toggleFold(): void {
    this.expanded.update((open) => !open);
  }
}
