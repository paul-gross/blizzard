import { ChangeDetectionStrategy, Component, input, output, signal } from '@angular/core';

import { DONE_LANE, KitAccordionSection } from 'fleet';
import type { BoardCard } from '../board-card/board-card';
import { BoardListRow } from './board-list-row';

/** One section of the list — one board lane's cards, in the board's own order for that lane. */
export interface BoardListSection {
  readonly key: string;
  readonly label: string;
  readonly count: number | null;
  readonly cards: readonly BoardCard[];
}

/**
 * The board read as a list: each lane a collapsible section of full-width rows, so a
 * chunk's titles have room to read in full. Sections arrive in the order they render;
 * DONE starts collapsed.
 *
 * Presentational only: selection leaves as {@link selectChunk}, exactly as a board card's does.
 */
@Component({
  selector: 'app-board-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BoardListRow, KitAccordionSection],
  templateUrl: './board-list.html',
  styleUrl: './board-list.css',
})
export class BoardList {
  readonly sections = input.required<readonly BoardListSection[]>();
  readonly selectedChunkId = input<string | null>(null);
  readonly gatedChunkIds = input<ReadonlySet<string>>(new Set());
  /** Whether the chunk read has answered — until then an empty section says nothing. */
  readonly loaded = input(false);

  readonly selectChunk = output<string>();

  private readonly collapsed = signal<ReadonlySet<string>>(new Set([DONE_LANE]));

  protected isCollapsed(key: string): boolean {
    return this.collapsed().has(key);
  }

  protected toggle(key: string): void {
    this.collapsed.update((keys) => {
      const next = new Set(keys);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  }
}
