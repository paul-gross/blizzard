import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import {
  BoardShell,
  ChunkDetail,
  FleetLiveUpdates,
  QueuePanel,
  RunnerStrip,
  injectHubChunksQuery,
  injectHubHealthQuery,
} from 'fleet';

/**
 * The hub board app — the mission-control fleet surface (D-048). It composes the
 * shared fleet library over live reads from the generated client (TanStack Query)
 * and the hub's SSE stream:
 *
 * - the {@link FleetLiveUpdates} spine subscribes to `GET /api/events/stream` and
 *   invalidates the reads on every hub fact, so the board streams live (D-097);
 * - {@link BoardShell} renders every chunk in its derived-status column (D-004);
 *   selecting a card fills the {@link ChunkDetail} dock beneath the board — node
 *   history, artifacts, and the human-loop actions (answer a question, resolve a
 *   gate, copy a takeover) — without reflowing the board columns;
 * - {@link QueuePanel} shapes the ready queue (prioritize + group); {@link RunnerStrip}
 *   shows the registry with pause/resume — the two operator controls (MVP criterion 11).
 */
@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BoardShell, ChunkDetail, QueuePanel, RunnerStrip],
  template: `
    <div class="layout">
      <div class="workspace">
        <fleet-board-shell class="board" [connection]="connection()" [chunks]="chunks()" (selectChunk)="selected.set($event)" />
        <fleet-queue-panel class="rail" />
      </div>
      @if (selected() !== null) {
        <fleet-chunk-detail class="dock" [chunkId]="selected()" (dismiss)="selected.set(null)" />
      }
      <fleet-runner-strip class="runners" />
    </div>
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
    }
    .layout {
      display: grid;
      grid-template-rows: 1fr auto;
      height: 100%;
      min-height: 0;
    }
    .workspace {
      display: grid;
      grid-template-columns: 1fr minmax(240px, 300px);
      min-height: 0;
    }
    .board {
      min-width: 0;
    }
    .rail {
      min-width: 0;
      overflow-y: auto;
      border-left: 1px solid var(--bezel);
    }
    /* Chunk detail docks along the bottom, spanning the full width beneath the
       board and rail. It is a layout-level row, not a workspace column, so filling
       or clearing it never resizes or shifts the board columns. */
    .dock {
      min-height: 0;
      height: clamp(220px, 34vh, 440px);
    }
  `,
})
export class App {
  private readonly health = injectHubHealthQuery();
  private readonly chunksQuery = injectHubChunksQuery();
  private readonly live = inject(FleetLiveUpdates);

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
