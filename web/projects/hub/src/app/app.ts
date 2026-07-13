import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';
import {
  BoardShell,
  ChunkDetailPanel,
  injectHubChunkDetailQuery,
  injectHubChunksQuery,
  injectHubHealthQuery,
} from 'fleet';

/**
 * The hub board app — renders the shared mission-control board shell over live reads
 * from the generated client (TanStack Query): the hub health query drives the header
 * connection status, and the chunk-list query feeds the board its chunks (derived
 * status + current node, D-004).
 *
 * Selecting a card opens the chunk detail drawer — one chunk's full aggregate (node
 * history + artifact store, D-036): the review that failed once and looped back to
 * build, and the artifacts it carried (product/mvp.md, MVP criterion 9/11). The
 * selected id drives a reactive detail query; dismissing the drawer clears it.
 */
@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BoardShell, ChunkDetailPanel],
  template: `
    <div class="layout" [class.has-detail]="detail() !== undefined">
      <fleet-board-shell [connection]="connection()" [chunks]="chunks()" (selectChunk)="selected.set($event)" />
      @if (detail(); as d) {
        <fleet-chunk-detail-panel [detail]="d" (dismiss)="selected.set(null)" />
      }
    </div>
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
    }
    .layout {
      display: grid;
      grid-template-columns: 1fr;
      height: 100%;
    }
    .layout.has-detail {
      grid-template-columns: 1fr minmax(280px, 360px);
    }
  `,
})
export class App {
  private readonly health = injectHubHealthQuery();
  private readonly chunksQuery = injectHubChunksQuery();

  /** The board card the operator opened, or `null` when the drawer is dismissed. */
  protected readonly selected = signal<string | null>(null);
  private readonly detailQuery = injectHubChunkDetailQuery(() => this.selected());

  /** Header status derived from the live health query state. */
  protected readonly connection = computed(() => {
    if (this.health.isPending()) return 'connecting…';
    if (this.health.isError()) return 'offline';
    return this.health.data()?.['status'] ?? 'ok';
  });

  /** The live fleet chunk list; empty until the first read resolves. */
  protected readonly chunks = computed(() => this.chunksQuery.data() ?? []);

  /** The selected chunk's detail, or `undefined` while nothing is open / still loading. */
  protected readonly detail = computed(() => (this.selected() === null ? undefined : this.detailQuery.data()));
}
