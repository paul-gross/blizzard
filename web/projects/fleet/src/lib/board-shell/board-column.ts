import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { CdkDrag, type CdkDragDrop, CdkDropList, moveItemInArray } from '@angular/cdk/drag-drop';

import { BoardCardComponent, type BoardCard } from '../board-card/board-card';
import type { Lane } from '../chunk-lanes';
import { KitBeacon } from '../kit/kit-beacon';
import { KitButton } from '../kit/kit-button';

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

/** A card's Top button, activated — the same `list` tagging as {@link BoardReposition}. */
export interface BoardTopMove {
  readonly chunkId: string;
  readonly list: BoardReorderList;
}

/**
 * One board column — a lane's engraved head (label, occupancy beacon, count)
 * over its card list. Split out of {@link BoardShell} (issue #137) alongside
 * {@link BoardCardComponent} so that file stays under the
 * `web:structural-gate` line cap once the READY lane grew its queue-shaping
 * affordances.
 *
 * Two independent conditionals arm this component's affordances: {@link reorderControls}
 * arms the drag-and-drop drop list and the per-card move-to-top control, and
 * {@link groupingControls} arms the per-card multi-select checkbox and the head's Group
 * button. READY sets both; BACKLOG sets {@link reorderControls} alone — it reorders
 * like READY does, but grouping stays READY-only. Every other lane renders a plain,
 * undraggable list, so `cdkDropList`/`cdkDrag` never reach markup that has no reorder
 * to express. The queue-shaping testids (`queue-select`, `group-selected`) are the
 * ones the retired left rail used: the controls moved onto the board, they were not
 * renamed. The Top button's testid is lane-scoped instead ({@link moveTopTestId}):
 * READY keeps `queue-move-top`, BACKLOG gets its own `backlog-move-top`, so each
 * resolves to exactly one lane's control.
 *
 * Presentational: the checkbox selection is plain UI state and lives here (the
 * same reason `QueuePanelView` held it before this replaced it,
 * `bzh:frontend-container-presentational`); everything else is a plain input,
 * and every action leaves as an output for a container to write.
 */
@Component({
  selector: 'fleet-board-column',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BoardCardComponent, CdkDrag, CdkDropList, KitBeacon, KitButton],
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

  /** Whether this lane carries the grouping affordances — the multi-select
   * checkbox and the Group button — set only for READY (grouping stays
   * READY-only, out of scope to extend). */
  readonly groupingControls = input(false);

  /** Whether this lane carries the reorder affordances — drag-and-drop and the
   * Top button — set for READY and BACKLOG alike, independently of {@link groupingControls}:
   * both lists rank independently (`bzh:ranking-is-per-list`) and both are
   * operator-reshapeable, but only READY groups. */
  readonly reorderControls = input(false);

  /** Whether the current identity may promote a backlog chunk (`chunk:control` —
   * issue #210), forwarded to each {@link BoardCardComponent}. */
  readonly canControl = input(false);

  /** Whether the current identity may reorder or group the ready queue and
   * backlog (`queue:reorder` — issue #210) — gates the Group button and,
   * combined with {@link reorderControls}, whether this lane's drag-and-drop is
   * armed at all. */
  readonly canReorder = input(false);

  /** Emitted with a chunk id when its card is activated — fills the detail dock. */
  readonly selectChunk = output<string>();

  /** Emitted with a chunk id when a backlog card's Promote is clicked. */
  readonly promote = output<string>();

  /** Emitted when a card is dropped somewhere new — the anchor it landed after,
   * tagged with which list ({@link BoardReorderList}) the move belongs to. */
  readonly reposition = output<BoardReposition>();

  /** Emitted when a card's Top button is clicked, tagged with which list the
   * move belongs to. */
  readonly moveToTop = output<BoardTopMove>();

  /** Emitted with the checked ids in current lane order (the top-most is the
   * group survivor) when the operator activates Group. */
  readonly group = output<readonly string[]>();

  /** Chunk ids checked for grouping. */
  private readonly selection = signal<ReadonlySet<string>>(new Set());

  /** Checked ids in current lane order (the top-most is the group survivor). */
  protected readonly selectedIds = computed<readonly string[]>(() => {
    const selected = this.selection();
    return this.cards()
      .map((card) => card.chunkId)
      .filter((chunkId) => selected.has(chunkId));
  });

  /** The beacon color for an occupied lane's header, or null for the quiet lanes:
   * amber for work in flight or parked on a human, red for an escalation. */
  protected readonly blink = computed<'amber' | 'red' | null>(() => {
    const key = this.column().key;
    if (key === 'running' || key === 'waiting') return 'amber';
    if (key === 'needs') return 'red';
    return null;
  });

  /** This lane's own reorder list tag, derived from {@link Lane.key} — the value
   * every {@link reposition}/{@link moveToTop} emission carries. Only meaningful
   * when {@link reorderControls} is armed (READY or BACKLOG); asserted rather
   * than defaulted, since a lane with no reorder never reads this. */
  private get list(): BoardReorderList {
    const key = this.column().key;
    if (key === 'ready' || key === 'notready') return key;
    throw new Error(`board column ${key} has no reorder list`);
  }

  /** The Top button's testid, lane-scoped so it resolves to exactly one
   * component: READY keeps `queue-move-top` (the retired left rail's own),
   * BACKLOG gets its own `backlog-move-top`. */
  protected readonly moveTopTestId = computed(() => (this.list === 'notready' ? 'backlog-move-top' : 'queue-move-top'));

  protected isSelected(chunkId: string): boolean {
    return this.selection().has(chunkId);
  }

  protected toggle(chunkId: string): void {
    this.selection.update((prev) => {
      const next = new Set(prev);
      if (next.has(chunkId)) next.delete(chunkId);
      else next.add(chunkId);
      return next;
    });
  }

  protected groupSelected(): void {
    const ids = this.selectedIds();
    if (ids.length < 2) return;
    this.group.emit(ids);
    this.selection.set(new Set());
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

  /** A card's Top button, clicked — tagged with this lane's {@link list}. */
  protected topClicked(chunkId: string): void {
    this.moveToTop.emit({ chunkId, list: this.list });
  }
}
