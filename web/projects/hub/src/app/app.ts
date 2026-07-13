import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { BoardShell, injectHubHealthQuery } from 'fleet';

/**
 * The hub board app — a thin entrypoint that renders the shared mission-control
 * board shell and proves the read plumbing by wiring the hub health query
 * (TanStack Query over the generated client) into the header connection status.
 */
@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BoardShell],
  template: `<fleet-board-shell [connection]="connection()" />`,
  styles: `:host { display: block; height: 100%; }`,
})
export class App {
  private readonly health = injectHubHealthQuery();

  /** Header status derived from the live health query state. */
  protected readonly connection = computed(() => {
    if (this.health.isPending()) return 'connecting…';
    if (this.health.isError()) return 'offline';
    return this.health.data()?.['status'] ?? 'ok';
  });
}
