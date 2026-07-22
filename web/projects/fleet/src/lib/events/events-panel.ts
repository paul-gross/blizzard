import { ChangeDetectionStrategy, Component, computed, output, signal } from '@angular/core';

import { EventsView } from './events-view';
import { injectHubEventsQuery } from './events.query';

/**
 * The Events tab's **container** (blizzard#125 Phase 4) — the board's operational
 * event feed (`GET /api/events`), distinct from the right rail's bounded
 * {@link EventLogPanel} SSE tee: this reads the hub's own persisted, filterable
 * event log, not the client-side ring the live spine keeps.
 *
 * Owns the severity/runner/chunk filter state as signals and the reactive query
 * over them, and renders the presentational {@link EventsView}. Follows
 * `questions-panel.ts`: a standalone `fleet-`prefixed, OnPush container over the
 * generated hub client (bzh:generated-client) via TanStack Query. The live-update
 * service re-reads this on `event-logged` and on an escalation-bearing
 * `chunk-changed`; the poll is the floor.
 */
@Component({
  selector: 'fleet-events-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [EventsView],
  template: `
    <fleet-events-view
      [events]="events()"
      [severity]="severity()"
      [loading]="query.isPending()"
      [error]="query.isError()"
      (selectChunk)="selectChunk.emit($event)"
      (filterChange)="onFilterChange($event)"
    />
  `,
})
export class EventsPanel {
  /** Emitted with a chunk id when a row's chunk deep-link is activated. */
  readonly selectChunk = output<string>();

  /** The active severity filter, or `null` for "every severity". */
  protected readonly severity = signal<string | null>(null);
  /** The active runner filter, or `null` — no chip UI yet, but the read stays
   * filter-ready for a future runner-scoped view. */
  protected readonly runnerId = signal<string | null>(null);
  /** The active chunk filter, or `null` — same reasoning as {@link runnerId}. */
  protected readonly chunkId = signal<string | null>(null);

  protected readonly query = injectHubEventsQuery(() => ({
    severity: this.severity(),
    runnerId: this.runnerId(),
    chunkId: this.chunkId(),
  }));

  /** The filtered event feed; empty until the first read resolves. */
  protected readonly events = computed(() => this.query.data() ?? []);

  protected onFilterChange(severity: string): void {
    this.severity.set(severity === '' ? null : severity);
  }
}
