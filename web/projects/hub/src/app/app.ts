import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { BoardShell, injectHubChunksQuery, injectHubHealthQuery } from 'fleet';

/**
 * The hub board app — a thin entrypoint that renders the shared mission-control
 * board shell over live reads from the generated client (TanStack Query): the hub
 * health query drives the header connection status, and the chunk-list query feeds
 * the board its chunks (derived status + current node, D-004).
 */
@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BoardShell],
  template: `<fleet-board-shell [connection]="connection()" [chunks]="chunks()" />`,
  styles: `:host { display: block; height: 100%; }`,
})
export class App {
  private readonly health = injectHubHealthQuery();
  private readonly chunksQuery = injectHubChunksQuery();

  /** Header status derived from the live health query state. */
  protected readonly connection = computed(() => {
    if (this.health.isPending()) return 'connecting…';
    if (this.health.isError()) return 'offline';
    return this.health.data()?.['status'] ?? 'ok';
  });

  /** The live fleet chunk list; empty until the first read resolves. */
  protected readonly chunks = computed(() => this.chunksQuery.data() ?? []);
}
