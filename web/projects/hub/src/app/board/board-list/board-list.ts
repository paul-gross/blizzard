import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { CdkDrag, type CdkDragDrop, CdkDropList } from '@angular/cdk/drag-drop';
import { NgTemplateOutlet } from '@angular/common';

import { ChunkListRow } from 'fleet';

import { type BoardCard, boardCardView } from '../board-card/board-card-view.model';
import type { BoardLaneMove, BoardReorderList, BoardReposition } from '../board-shell/board-column';
import { resolveDrop } from '../board-shell/board-drop.model';
import { LaneIcon } from '../lane-icon/lane-icon';
import { ReorderGrip } from '../reorder-grip/reorder-grip';
import { BoardListRowSkeleton } from './board-list-row-skeleton';

/** One row of the list: a chunk's card and the board lane it sits in. */
export interface BoardListEntry {
  readonly lane: string;
  readonly card: BoardCard;
}

/** The list's rows in the order they render: what wants a person and work in flight, then the
 * two ranked lists — READY over BACKLOG, each in its hub order — then DONE. */
export interface BoardListGroups {
  readonly live: readonly BoardListEntry[];
  readonly ready: readonly BoardListEntry[];
  readonly backlog: readonly BoardListEntry[];
  readonly done: readonly BoardListEntry[];
}

const NO_GROUPS: BoardListGroups = { live: [], ready: [], backlog: [], done: [] };

/** The DOM id of a ranked list's drop target in the list layout — what the other one connects to. */
export function listDropId(list: BoardReorderList): string {
  return `board-list-drop-${list}`;
}

/**
 * The board read as a list: one full-width row per chunk, its lane drawn as an icon in the first
 * column, so a chunk's titles have room to read in full. READY and BACKLOG sit together, split by
 * a marked line, each in its hub order; an identity holding `queue:reorder` drags their rows to
 * reorder either list, and — with `chunk:control` too — across the line to promote or demote.
 *
 * Presentational only: selection, reorders, and moves leave as outputs, exactly as the lane
 * grid's do.
 */
@Component({
  selector: 'app-board-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BoardListRowSkeleton, CdkDrag, ChunkListRow, CdkDropList, LaneIcon, NgTemplateOutlet, ReorderGrip],
  templateUrl: './board-list.html',
  styleUrl: './board-list.css',
})
export class BoardList {
  readonly groups = input<BoardListGroups>(NO_GROUPS);
  readonly selectedChunkId = input<string | null>(null);
  readonly gatedChunkIds = input<ReadonlySet<string>>(new Set());
  /** How many row skeletons stand in for the rows while the chunk read is loading — 0 once it
   * has answered. */
  readonly skeletonRows = input(0);

  /** Whether the current identity may reorder the ready queue or backlog (`queue:reorder`) —
   * arms the READY and BACKLOG rows' drag. */
  readonly canReorder = input(false);

  /** Whether the current identity may promote or demote (`chunk:control`) — with
   * {@link canReorder}, lets a row cross the line between READY and BACKLOG. */
  readonly canControl = input(false);

  readonly selectChunk = output<string>();

  /** Emitted when a row is dropped somewhere new within its own ranked list. */
  readonly reposition = output<BoardReposition>();

  /** Emitted when a row is dropped across the line into the other ranked list. */
  readonly laneMove = output<BoardLaneMove>();

  protected readonly readyDropId = listDropId('ready');
  protected readonly backlogDropId = listDropId('notready');

  /** Each row's card as the fleet row draws it, by chunk id. */
  protected readonly views = computed(() => {
    const groups = this.groups();
    const entries = [...groups.live, ...groups.ready, ...groups.backlog, ...groups.done];
    return new Map(entries.map((entry) => [entry.card.chunkId, boardCardView(entry.card)]));
  });

  /** Whether any row renders at all. */
  protected readonly hasRows = computed(() => {
    const groups = this.groups();
    return groups.live.length + groups.ready.length + groups.backlog.length + groups.done.length > 0;
  });

  /** The other ranked list a list's drop target connects to — none unless a row may cross. */
  protected connectedTo(list: BoardReorderList): string[] {
    return this.canReorder() && this.canControl() ? [listDropId(list === 'ready' ? 'notready' : 'ready')] : [];
  }

  /** One index per skeleton row, for the template's loop. */
  protected readonly skeletonIndexes = computed(() => Array.from({ length: this.skeletonRows() }, (_, i) => i));

  /** A row dropped into `list`, resolved (`resolveDrop`) to the anchor it now sits after. */
  protected dropped(list: BoardReorderList, event: CdkDragDrop<unknown>): void {
    const entries = list === 'ready' ? this.groups().ready : this.groups().backlog;
    const ids = entries.map((entry) => entry.card.chunkId);
    const crossed = event.previousContainer !== event.container;
    const resolved = resolveDrop(list, ids, {
      chunkId: crossed ? String(event.item.data) : ids[event.previousIndex],
      previousIndex: event.previousIndex,
      currentIndex: event.currentIndex,
      crossed,
    });
    if (resolved?.kind === 'lane') this.laneMove.emit(resolved.move);
    if (resolved?.kind === 'reposition') this.reposition.emit(resolved.move);
  }
}
