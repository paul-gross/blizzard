import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';
import {
  BoardShell,
  ChunkDetail,
  EventLogPanel,
  QuestionsPanel,
  QueuePanel,
  RunnerPanel,
  injectHubChunksQuery,
  injectPromoteChunkMutation,
} from 'fleet';

/**
 * The board route — the three-column mission-control surface extracted verbatim
 * from the app root ({@link App}) when the router landed. Behavior is unchanged:
 *
 * - the **left rail** holds {@link QueuePanel}, which shapes the ready queue
 *   (prioritize + group), over {@link EventLogPanel}'s live feed;
 * - the **centre** stacks {@link BoardShell} — every chunk in its derived-status
 *   column — over the {@link ChunkDetail} dock. The dock is always mounted:
 *   selecting a card fills it (the work item, node history, artifacts, and the
 *   human-loop actions) and deselecting clears it to a rest state, so the board
 *   never resizes or reflows;
 * - the **right rail** holds {@link RunnerPanel}, the registry with pause/resume
 *   (MVP criterion 11), over {@link QuestionsPanel}, the fleet's open agent asks —
 *   clicking one opens its chunk in the dock, where it is answered.
 *
 * The titlebar, the {@link FleetLiveUpdates} spine, and the TanStack `QueryClient`
 * stay at the app root — none of them move here, so navigating away from and back
 * to `/board` never restarts the SSE stream or drops the query cache.
 */
@Component({
  selector: 'app-board-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BoardShell, ChunkDetail, EventLogPanel, QuestionsPanel, QueuePanel, RunnerPanel],
  template: `
    <main class="main">
      <div class="col rail-left">
        <fleet-queue-panel class="rail-queue" />
        <fleet-event-log-panel class="rail-log" />
      </div>
      <div class="col col-center">
        <fleet-board-shell
          class="board"
          [chunks]="chunks()"
          [selectedChunkId]="selected()"
          (selectChunk)="selected.set($event)"
          (promote)="promoteChunk.mutate({ chunkId: $event })"
        />
        <fleet-chunk-detail class="dock" [chunkId]="selected()" (dismiss)="selected.set(null)" />
      </div>
      <div class="col rail-right">
        <fleet-runner-panel />
        <fleet-questions-panel (selectChunk)="selected.set($event)" />
      </div>
    </main>
  `,
  styles: `
    :host {
      display: block;
      flex: 1;
      min-height: 0;
    }
    .main {
      height: 100%;
      min-height: 0;
      display: grid;
      /* 330px rails, but allowed to give ground on a narrow window:
         held rigid they starve the board, which is the column that matters. */
      grid-template-columns: minmax(260px, 330px) 1fr minmax(260px, 330px);
      gap: 6px;
      padding: 6px;
    }
    .col {
      display: flex;
      flex-direction: column;
      gap: 6px;
      min-height: 0;
      min-width: 0;
    }
    /* The rails run the full height of the workspace; the ready queue takes the
       larger share and the event log the rest, each scrolling its own body. */
    .rail-queue {
      flex: 1.35;
      min-height: 0;
      overflow-y: auto;
    }
    .rail-log {
      flex: 1;
      min-height: 0;
    }
    /* The centre column stacks the board over the chunk detail, so the detail sits
       to the right of the rails rather than spanning the window beneath them. Both
       are permanently mounted and hold their share of the column, so selecting or
       clearing a chunk never resizes or reflows the board. */
    .board {
      flex: 1.15;
      min-height: 0;
      min-width: 0;
    }
    .dock {
      flex: 1;
      min-height: 0;
      min-width: 0;
    }
  `,
})
export class BoardPage {
  private readonly chunksQuery = injectHubChunksQuery();

  /** Promote a not-ready chunk to ready from its board card. */
  protected readonly promoteChunk = injectPromoteChunkMutation();

  /** The board card the operator opened, or `null` when the dock is dismissed. */
  protected readonly selected = signal<string | null>(null);

  /** The live fleet chunk list; empty until the first read resolves. */
  protected readonly chunks = computed(() => this.chunksQuery.data() ?? []);
}
