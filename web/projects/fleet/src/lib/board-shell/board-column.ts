import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { CdkDrag, type CdkDragDrop, CdkDropList, moveItemInArray } from '@angular/cdk/drag-drop';

import { BoardCardComponent, type BoardCard } from '../board-card/board-card';
import type { Lane } from '../chunk-lanes';
import { KitBeacon } from '../kit/kit-beacon';

/**
 * Which hub-ranked list a reorder targets — the READY queue or the BACKLOG
 * (`not_ready`) list, ranked independently (`bzh:ranking-is-per-list`). Named
 * off {@link Lane.key} ('ready'/'notready') rather than a bespoke vocabulary, so
 * a container can route the event straight to the matching mutation.
 */
export type BoardReorderList = 'ready' | 'notready';

/**
 * A single-chunk reorder within one list — `chunk_id` lands immediately after
 * `afterChunkId`, or at the very top when that is `null`. The shape both
 * `POST /api/queue/position` and `POST /api/backlog/position` take, expressed
 * as an anchor rather than an index so a concurrent enqueue between read and
 * write cannot silently retarget it. `list` names which of the two lists this
 * move belongs to, so a container with both lanes armed can route it to the
 * right mutation.
 */
export interface BoardReposition {
  readonly chunkId: string;
  readonly afterChunkId: string | null;
  readonly list: BoardReorderList;
}

/**
 * One board column — a lane's engraved head (label, occupancy beacon, count)
 * over its card list.
 *
 * {@link reorderControls} arms READY and BACKLOG's drag-and-drop branches. Every
 * other lane renders a plain, undraggable list, so `cdkDropList`/`cdkDrag` never
 * reach markup that has no reorder to express. Armed cards carry a decorative grip;
 * the wrapper remains the whole-card drag target.
 *
 * Presentational: every value is a plain input and every action leaves as an
 * output for a container to write.
 */
@Component({
  selector: 'fleet-board-column',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BoardCardComponent, CdkDrag, CdkDropList, KitBeacon],
  host: { '[attr.data-col]': 'column().key' },
  templateUrl: './board-column.html',
  styleUrl: './board-column.css',
})
export class BoardColumn {
  /** The lane this column renders — its key, engraved label, and stat label. */
  readonly column = input.required<Lane>();

  /** The lane's cards, already in the order they should render. */
  readonly cards = input<readonly BoardCard[]>([]);

  /** The chunk whose detail fills the dock, or null — its card carries the highlight. */
  readonly selectedChunkId = input<string | null>(null);

  /** Whether this lane carries reordering — set for READY and BACKLOG alike:
   * both lists rank independently (`bzh:ranking-is-per-list`) and both are
   * operator-reshapeable. */
  readonly reorderControls = input(false);

  /** Whether the current identity may promote a backlog chunk (`chunk:control` —
   * issue #210), forwarded to each {@link BoardCardComponent}. */
  readonly canControl = input(false);

  /** Whether the current identity may reorder the ready queue and backlog
   * (`queue:reorder` — issue #210), combined with {@link reorderControls} to
   * arm this lane's drag-and-drop. */
  readonly canReorder = input(false);

  /** Emitted with a chunk id when its card is activated — fills the detail dock. */
  readonly selectChunk = output<string>();

  /** Emitted with a chunk id when a backlog card's Promote is clicked. */
  readonly promote = output<string>();

  /** Emitted when a card is dropped somewhere new — the anchor it landed after,
   * tagged with which list ({@link BoardReorderList}) the move belongs to. */
  readonly reposition = output<BoardReposition>();

  /** The beacon color for an occupied lane's header, or null for the quiet lanes:
   * amber for work in flight or parked on a human, red for an escalation. */
  protected readonly blink = computed<'amber' | 'red' | null>(() => {
    const key = this.column().key;
    if (key === 'running' || key === 'waiting') return 'amber';
    if (key === 'needs') return 'red';
    return null;
  });

  /** This lane's own reorder list tag, derived from {@link Lane.key}. Only meaningful
   * when {@link reorderControls} is armed (READY or BACKLOG); asserted rather
   * than defaulted, since a lane with no reorder never reads this. */
  private get list(): BoardReorderList {
    const key = this.column().key;
    if (key === 'ready' || key === 'notready') return key;
    throw new Error(`board column ${key} has no reorder list`);
  }

  /**
   * A dropped card, resolved to the anchor it now sits after, tagged with this
   * lane's {@link list}.
   *
   * The rendered order is replayed on a copy (cdk moves nothing itself) so the
   * anchor is the chunk that ends up immediately *above* the dropped one — a
   * drop at the top has none, which is the `null` the hub reads as "very top".
   * A drop that changed nothing writes nothing.
   */
  protected dropped(event: CdkDragDrop<unknown>): void {
    if (event.previousIndex === event.currentIndex) return;
    const ids = this.cards().map((card) => card.chunkId);
    moveItemInArray(ids, event.previousIndex, event.currentIndex);
    this.reposition.emit({
      chunkId: ids[event.currentIndex],
      afterChunkId: event.currentIndex === 0 ? null : ids[event.currentIndex - 1],
      list: this.list,
    });
  }

}
