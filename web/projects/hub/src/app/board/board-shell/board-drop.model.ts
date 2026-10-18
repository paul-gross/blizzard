import { moveItemInArray } from '@angular/cdk/drag-drop';

import type { BoardLaneMove, BoardReorderList, BoardReposition } from './board-column';

/** A drop into one ranked list, as cdk reports it: the dragged chunk, where it sat, where it
 * landed, and whether it came from the other ranked list. */
export interface BoardDrop {
  readonly chunkId: string;
  readonly previousIndex: number;
  readonly currentIndex: number;
  readonly crossed: boolean;
}

/** What a drop asks the hub for: a reorder within the list, or a move into it from the other. */
export type BoardDropMove =
  | { readonly kind: 'reposition'; readonly move: BoardReposition }
  | { readonly kind: 'lane'; readonly move: BoardLaneMove };

/**
 * A drop into `list`, whose chunks render in `ids` order, resolved to the anchor the chunk now
 * sits after — `null` at the very top, which the hub reads as "first". A drop from the other
 * list is a lane move; one within the list replays the order on a copy (cdk moves nothing
 * itself) so the anchor is the chunk that ends up immediately above. A drop that changed
 * nothing resolves to `null`.
 */
export function resolveDrop(list: BoardReorderList, ids: readonly string[], drop: BoardDrop): BoardDropMove | null {
  if (drop.crossed) {
    return {
      kind: 'lane',
      move: {
        chunkId: drop.chunkId,
        afterChunkId: drop.currentIndex === 0 ? null : ids[drop.currentIndex - 1],
        from: list === 'ready' ? 'notready' : 'ready',
        to: list,
      },
    };
  }
  if (drop.previousIndex === drop.currentIndex) return null;
  const order = [...ids];
  moveItemInArray(order, drop.previousIndex, drop.currentIndex);
  return {
    kind: 'reposition',
    move: { chunkId: order[drop.currentIndex], afterChunkId: drop.currentIndex === 0 ? null : order[drop.currentIndex - 1], list },
  };
}
