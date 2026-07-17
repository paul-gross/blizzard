import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import {
  BoardHeader,
  BoardShell,
  ChunkDetail,
  EventLogPanel,
  FleetLiveUpdates,
  QuestionsPanel,
  QueuePanel,
  RunnerPanel,
  injectHubChunksQuery,
  injectHubHealthQuery,
  injectPromoteChunkMutation,
} from 'fleet';

/**
 * The hub board app — the mission-control fleet surface (D-048). It composes the
 * shared fleet library over live reads from the generated client (TanStack Query)
 * and the hub's SSE stream:
 *
 * The window is the mockup's three columns under a full-width titlebar
 * ({@link BoardHeader} — the brand, the live fleet counts, and the hub connection):
 *
 * - the **left rail** holds {@link QueuePanel}, which shapes the ready queue
 *   (prioritize + group), over {@link EventLogPanel}'s live feed;
 * - the **centre** stacks {@link BoardShell} — every chunk in its derived-status
 *   column (D-004) — over the {@link ChunkDetail} dock. The dock is always mounted:
 *   selecting a card fills it (the work item, node history, artifacts, and the
 *   human-loop actions) and deselecting clears it to a rest state, so the board
 *   never resizes or reflows;
 * - the **right rail** holds {@link RunnerPanel}, the registry with pause/resume
 *   (MVP criterion 11), over {@link QuestionsPanel}, the fleet's open agent asks —
 *   clicking one opens its chunk in the dock, where it is answered;
 * - the {@link FleetLiveUpdates} spine subscribes to `GET /api/events/stream` and
 *   invalidates the reads on every hub fact, so the whole board streams live (D-097).
 */
@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BoardHeader, BoardShell, ChunkDetail, EventLogPanel, QuestionsPanel, QueuePanel, RunnerPanel],
  template: `
    <div class="layout">
      <fleet-board-header [connection]="connection()" [chunks]="chunks()" />
      <main class="main">
        <div class="col rail-left">
          <fleet-queue-panel class="rail-queue" />
          <fleet-event-log-panel class="rail-log" />
        </div>
        <div class="col col-center">
          <fleet-board-shell
            class="board"
            [chunks]="chunks()"
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
    </div>
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
    }
    /* The titlebar spans the window, and the three columns fill everything under
       it (the mockup's 330px / 1fr / 330px main grid). The layout is height-capped
       to the viewport and every panel scrolls its own body, so the page itself
       never scrolls — an operator's board does not move under them. */
    .layout {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
      overflow: hidden;
    }
    .main {
      flex: 1;
      min-height: 0;
      display: grid;
      /* The mockup's 330px rails, but allowed to give ground on a narrow window:
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
    /* The rails run the full height of the workspace, from the titlebar to the
       bottom of the window; the ready queue takes the larger share and the event
       log the rest, each scrolling its own body. */
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
export class App {
  private readonly health = injectHubHealthQuery();
  private readonly chunksQuery = injectHubChunksQuery();
  private readonly live = inject(FleetLiveUpdates);

  /** Promote a not-ready chunk to ready from its board card (D-103). */
  protected readonly promoteChunk = injectPromoteChunkMutation();

  constructor() {
    // Open the SSE stream and wire it to the query cache for the app's lifetime.
    this.live.start();
  }

  /** The board card the operator opened, or `null` when the dock is dismissed. */
  protected readonly selected = signal<string | null>(null);

  /** Header status: the live stream's connection state, falling back to the health read. */
  protected readonly connection = computed(() => {
    const streamState = this.live.status();
    if (streamState === 'reconnecting') return 'reconnecting…';
    if (this.health.isPending()) return 'connecting…';
    if (this.health.isError()) return 'offline';
    return this.health.data()?.['status'] ?? 'ok';
  });

  /** The live fleet chunk list; empty until the first read resolves. */
  protected readonly chunks = computed(() => this.chunksQuery.data() ?? []);
}
