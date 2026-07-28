import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { CdkDrag, type CdkDragDrop, CdkDropList, moveItemInArray } from '@angular/cdk/drag-drop';

import { BoardCardComponent, type BoardCard } from '../board-card/board-card';
import type { Lane } from '../chunk-lanes';
import { KitBeacon } from '../kit/kit-beacon';
import { KitButton } from '../kit/kit-button';

/**
 * A single-chunk queue reposition — `chunk_id` lands immediately after
 * `afterChunkId`, or at the very top when that is `null`. The shape the hub's
 * `POST /api/queue/position` takes, expressed as an anchor rather than an index
 * so a concurrent enqueue between read and write cannot silently retarget it.
 */
export interface BoardReposition {
  readonly chunkId: string;
  readonly afterChunkId: string | null;
}

/**
 * One board column — a lane's engraved head (label, occupancy beacon, count)
 * over its card list. Split out of {@link BoardShell} (issue #137) alongside
 * {@link BoardCardComponent} so that file stays under the
 * `web:structural-gate` line cap once the READY lane grew its queue-shaping
 * affordances.
 *
 * Those affordances are this component's one conditional: {@link queueControls}
 * arms the drag-and-drop drop list, the per-card move-to-top and multi-select
 * controls, and the head's Group button. Only the READY lane sets it — every
 * other lane renders a plain, undraggable list, so `cdkDropList`/`cdkDrag` never
 * reach markup that has no reorder to express. The queue-shaping testids
 * (`queue-move-top`, `queue-select`, `group-selected`) are the ones the retired
 * left rail used: the controls moved onto the board, they were not renamed.
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
  template: `
    <div class="b-col-head">
      <span class="col-lbl">{{ column().label }}</span>
      @if (queueControls()) {
        <fleet-kit-button
          variant="primary"
          testid="group-selected"
          [disabled]="selectedIds().length < 2"
          (click)="groupSelected()"
        >
          Group ({{ selectedIds().length }})
        </fleet-kit-button>
      }
      <span class="n">
        <!-- A live lane with occupants announces itself: a flashing square
             ahead of the count — amber for work in flight or parked on a
             human, red for an escalation. Quiet (empty) lanes show none. -->
        @if (cards().length > 0 && blink(); as tone) {
          <fleet-kit-beacon data-testid="lane-blink" [attr.data-blink]="tone" [active]="true" [tone]="tone" />
        }
        {{ cards().length }}
      </span>
    </div>
    @if (queueControls()) {
      <div class="b-col-body" cdkDropList (cdkDropListDropped)="dropped($event)">
        @for (card of cards(); track card.chunkId; let i = $index) {
          <div class="q-card" cdkDrag>
            <div class="q-ctl">
              <input
                type="checkbox"
                class="sel"
                data-testid="queue-select"
                [attr.aria-label]="'Select ' + card.chunkId + ' for grouping'"
                [checked]="isSelected(card.chunkId)"
                (change)="toggle(card.chunkId)"
              />
              <fleet-kit-button
                testid="queue-move-top"
                [ariaLabel]="'Move ' + card.chunkId + ' to top'"
                [disabled]="i === 0"
                (click)="moveToTop.emit(card.chunkId)"
              >
                Top
              </fleet-kit-button>
            </div>
            <fleet-board-card
              [card]="card"
              [selected]="card.chunkId === selectedChunkId()"
              (selectChunk)="selectChunk.emit($event)"
              (promote)="promote.emit($event)"
            />
          </div>
        }
      </div>
    } @else {
      <div class="b-col-body">
        @for (card of cards(); track card.chunkId) {
          <fleet-board-card
            [card]="card"
            [selected]="card.chunkId === selectedChunkId()"
            (selectChunk)="selectChunk.emit($event)"
            (promote)="promote.emit($event)"
          />
        }
      </div>
    }
  `,
  styles: `
    :host {
      background: var(--panel-deep);
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    /* The column's engraved heading — see BoardShell's own note on why this rule
       is local rather than shared: content in a different encapsulation scope
       cannot be reached by a rule declared there. */
    .col-lbl {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
      text-shadow: 0 1px 0 var(--overlay-90);
    }
    .b-col-head {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 6px;
      padding: 4px 6px;
      border-bottom: 1px solid var(--line);
      flex: none;
    }
    .b-col-head .n {
      display: flex;
      align-items: center;
      gap: 5px;
      font-size: var(--fs-md);
      color: var(--label-dim);
    }
    /* Per the mockup, the header labels stay uniformly grey and the **count** carries
       each lane's color: amber for live work (running, parked on a human), red for an
       escalation, green for done — all from tokens. */
    :host([data-col='running']) .b-col-head .n,
    :host([data-col='waiting']) .b-col-head .n {
      color: var(--amber);
    }
    :host([data-col='needs']) .b-col-head .n {
      color: var(--red);
    }
    :host([data-col='done']) .b-col-head .n {
      color: var(--green);
    }
    /* The DONE column keeps its green head accent; its cards' own accent is
       BoardCardComponent's own (issue #137), keyed off the card's status
       rather than reaching down from here. */
    :host([data-col='done']) .b-col-head {
      border-bottom-color: var(--green-dim);
    }
    /* The BACKLOG column's header reads as held/inert: a muted label, distinct
       from the READY lane beside it and from any live lane. Colors come from
       tokens, never hard-coded hex. */
    :host([data-col='notready']) .b-col-head .col-lbl {
      color: var(--label-dim);
    }
    /* READY is the dispatch queue: its head reads in the cyan the board uses for
       operator-shapeable surfaces, so the one column that takes a drag is the one
       that looks like it does. */
    :host([data-col='ready']) .b-col-head .col-lbl {
      color: var(--cyan);
    }
    .b-col-body {
      overflow-y: auto;
      overflow-x: hidden;
      flex: 1;
      padding: 4px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    /* A READY card and its queue controls travel together as one draggable
       block — the controls sit above the card rather than inside it, so
       BoardCardComponent stays the same presentational card in every lane. */
    .q-card {
      display: flex;
      flex-direction: column;
      gap: 2px;
      cursor: grab;
    }
    .q-ctl {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 6px;
    }
    .sel {
      margin: 0;
      accent-color: var(--cyan);
    }
    /* The gap holding a dragged card's destination. cdk's *preview* is appended
       to the body, outside this component's encapsulation, so it is left to the
       cdk's own defaults rather than styled from a rule that could never reach
       it; the placeholder stays inside the list and this does reach. */
    .cdk-drag-placeholder {
      opacity: 0.35;
    }
  `,
})
export class BoardColumn {
  /** The lane this column renders — its key, engraved label, and stat label. */
  readonly column = input.required<Lane>();

  /** The lane's cards, already in the order they should render. */
  readonly cards = input<readonly BoardCard[]>([]);

  /** The chunk whose detail fills the dock, or null — its card carries the highlight. */
  readonly selectedChunkId = input<string | null>(null);

  /** Whether this lane carries the queue-shaping affordances — set only for READY. */
  readonly queueControls = input(false);

  /** Emitted with a chunk id when its card is activated — fills the detail dock. */
  readonly selectChunk = output<string>();

  /** Emitted with a chunk id when a backlog card's Promote is clicked. */
  readonly promote = output<string>();

  /** Emitted when a READY card is dropped somewhere new — the anchor it landed after. */
  readonly reposition = output<BoardReposition>();

  /** Emitted with a chunk id when a READY card's Top button is clicked. */
  readonly moveToTop = output<string>();

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
   * A dropped READY card, resolved to the anchor it now sits after.
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
    });
  }
}
