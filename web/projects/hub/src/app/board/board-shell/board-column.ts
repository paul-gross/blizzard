import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { CdkDrag, type CdkDragDrop, CdkDropList } from '@angular/cdk/drag-drop';

import { type BoardCard, boardCardView } from '../board-card/board-card-view.model';
import { BoardCardSkeleton } from '../board-card/board-card-skeleton';
import { ChunkCard, type Lane, KitBeacon, type Tone, laneTone, toneColor } from 'fleet';
import { ReorderGrip } from '../reorder-grip/reorder-grip';
import { resolveDrop } from './board-drop.model';

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
 * A card dropped into the other ranked list — `chunkId` leaves `from` for `to`, landing immediately
 * after `afterChunkId` in the destination, or at its very top when that is `null`. The container
 * turns it into a promote (into READY) or a demote (into BACKLOG).
 */
export interface BoardLaneMove {
  readonly chunkId: string;
  readonly afterChunkId: string | null;
  readonly from: BoardReorderList;
  readonly to: BoardReorderList;
}

/** The DOM id of a ranked lane's drop list — what the other lane's list connects to. */
export function laneDropId(list: BoardReorderList): string {
  return `board-drop-${list}`;
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
  selector: 'app-board-column',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BoardCardSkeleton, ChunkCard, CdkDrag, CdkDropList, KitBeacon, ReorderGrip],
  host: { '[attr.data-col]': 'column().key' },
  templateUrl: './board-column.html',
  styleUrl: './board-column.css',
})
export class BoardColumn {
  /** The lane this column renders — its key, engraved label, and stat label. */
  readonly column = input.required<Lane>();

  /** The lane's cards, already in the order they should render. */
  readonly cards = input<readonly BoardCard[]>([]);

  /** How many card skeletons stand in for the lane's cards while the chunk read is loading —
   * 0 once it has answered. */
  readonly skeletons = input(0);

  /** Each card as the fleet card draws it. */
  protected readonly views = computed(() => this.cards().map(boardCardView));

  /** One index per skeleton, for the template's loop. */
  protected readonly skeletonIndexes = computed(() => Array.from({ length: this.skeletons() }, (_, i) => i));

  /** The lane's all-time count, or null before the counts read resolves — the head
   * withholds the number rather than show the card list's length, which the board's
   * window truncates. */
  readonly count = input<number | null>(null);

  /** The head's hover text, or `null` for none. */
  readonly headTitle = input<string | null>(null);

  /** The chunk whose detail fills the dock, or null — its card carries the highlight. */
  readonly selectedChunkId = input<string | null>(null);

  /** Every chunk id with an open decision — each such card carries the `gate` marker. */
  readonly gatedChunkIds = input<ReadonlySet<string>>(new Set());

  /** Whether this lane carries reordering — set for READY and BACKLOG alike:
   * both lists rank independently (`bzh:ranking-is-per-list`) and both are
   * operator-reshapeable. */
  readonly reorderControls = input(false);

  /** Whether the current identity may promote a backlog chunk (`chunk:control`),
   * forwarded to each card. */
  readonly canControl = input(false);

  /** Whether the current identity may reorder the ready queue and backlog
   * (`queue:reorder`), combined with {@link reorderControls} to
   * arm this lane's drag-and-drop. */
  readonly canReorder = input(false);

  /** Emitted with a chunk id when its card is activated — fills the detail dock. */
  readonly selectChunk = output<string>();

  /** Emitted with a chunk id when a backlog card's Promote is clicked. */
  readonly promote = output<string>();

  /** Emitted when a card is dropped somewhere new — the anchor it landed after,
   * tagged with which list ({@link BoardReorderList}) the move belongs to. */
  readonly reposition = output<BoardReposition>();

  /** Emitted when a card from the other ranked list is dropped here. */
  readonly laneMove = output<BoardLaneMove>();

  /** This lane's drop list id, and the other ranked lane's the list connects to while
   * {@link crossLane} holds — none otherwise, so a board without both permissions has no
   * connected lists. */
  protected readonly dropId = computed(() => laneDropId(this.list));
  protected readonly connectedTo = computed<string[]>(() =>
    this.crossLane() ? [laneDropId(this.list === 'ready' ? 'notready' : 'ready')] : [],
  );

  /** Whether a card may cross between READY and BACKLOG — a demote or promote
   * (`chunk:control`) from lists that exist at all (`queue:reorder`). */
  protected readonly crossLane = computed(() => this.reorderControls() && this.canReorder() && this.canControl());

  /** The count's color — its lane's tone. */
  protected readonly countColor = computed(() => toneColor(laneTone(this.column().key)));

  /** The beacon tone for an occupied lane's header, or null for the quiet lanes: the
   * lanes with work in flight, parked on a human, or escalated beacon, in their lane's tone. */
  protected readonly blink = computed<Tone | null>(() => {
    const key = this.column().key;
    return key === 'running' || key === 'waiting' || key === 'needs' ? laneTone(key) : null;
  });

  /** This lane's own reorder list tag, derived from {@link Lane.key}. Only meaningful
   * when {@link reorderControls} is armed (READY or BACKLOG); asserted rather
   * than defaulted, since a lane with no reorder never reads this. */
  private get list(): BoardReorderList {
    const key = this.column().key;
    if (key === 'ready' || key === 'notready') return key;
    throw new Error(`board column ${key} has no reorder list`);
  }

  /** A dropped card, resolved (`resolveDrop`) to the anchor it now sits after, tagged with this
   * lane's {@link list}. A drop that changed nothing writes nothing. */
  protected dropped(event: CdkDragDrop<unknown>): void {
    const ids = this.cards().map((card) => card.chunkId);
    const crossed = event.previousContainer !== event.container;
    const resolved = resolveDrop(this.list, ids, {
      chunkId: crossed ? String(event.item.data) : ids[event.previousIndex],
      previousIndex: event.previousIndex,
      currentIndex: event.currentIndex,
      crossed,
    });
    if (resolved?.kind === 'lane') this.laneMove.emit(resolved.move);
    if (resolved?.kind === 'reposition') this.reposition.emit(resolved.move);
  }

}
