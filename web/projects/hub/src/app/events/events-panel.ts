import { ChangeDetectionStrategy, Component, computed, output, signal } from '@angular/core';

import { type KitAsyncStateValue, asyncState } from 'fleet';
import { injectQueryFilters } from '../core/route-state';
import { EventsView } from './events-view';
import { eventChunkIds, eventRunnerIds } from './events-panel.model';
import { type EventSeverity, injectHubEventsQuery, narrowEventSeverity } from './events.query';

/**
 * The Events tab's **container** — the board's operational
 * event feed (`GET /api/events`), distinct from the right rail's {@link ActivityPanel}:
 * this reads the hub's own persisted, filterable event log (severity/runner/chunk
 * filters, capped only by `limit`).
 *
 * Owns the severity/runner filter state as signals, the chunk filter in the URL's
 * `?chunk=` (so a chunk's own page can deep-link its events, and the filtered view
 * survives a reload), and the reactive query over them, and renders the presentational {@link EventsView}. Follows
 * `questions-panel.ts`: a standalone `fleet-`prefixed, OnPush container over the
 * generated hub client (bzh:generated-client) via TanStack Query (freshness:
 * {@link injectHubEventsQuery}).
 *
 * The runner and chunk filter axes are open sets, so their chip **universe** is
 * derived here (`runnerIds`/`chunkIds`) rather than in the view. It comes from a
 * second, **severity-only** read (`optionsQuery`) — deliberately NOT narrowed by the
 * runner/chunk selection, so picking a runner never makes the other runner chips
 * vanish. When no runner/chunk filter is active the two reads share a query key and
 * TanStack collapses them to one fetch; only an active runner/chunk filter splits
 * them.
 */
@Component({
  selector: 'app-events-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [EventsView],
  templateUrl: './events-panel.html',
})
export class EventsPanel {
  /** Emitted with a chunk id when a row's chunk deep-link is activated. */
  readonly selectChunk = output<string>();

  /** The active severity filter, or `null` for "every severity". */
  protected readonly severity = signal<EventSeverity | null>(null);
  /** The active runner filter, or `null` for "every runner". */
  protected readonly runnerId = signal<string | null>(null);
  private readonly filters = injectQueryFilters();

  /** The active chunk filter, or `null` for "every chunk" — read from `?chunk=` and
   * sent to the hub as `chunk_id`, never applied client-side. */
  protected readonly chunkId = computed(() => this.filters.read('chunk'));

  protected readonly query = injectHubEventsQuery(() => ({
    severity: this.severity(),
    runnerId: this.runnerId(),
    chunkId: this.chunkId(),
  }));

  /** The severity-only read backing the runner/chunk chip universe (see the class
   * doc): it ignores the runner/chunk selection so those chips stay stable. */
  private readonly optionsQuery = injectHubEventsQuery(() => ({ severity: this.severity() }));

  /** The filtered event feed; empty until the first read resolves. */
  protected readonly events = computed(() => this.query.data() ?? []);

  /** The feed's async state (AC 5) — derived from the filtered read alone; the
   * severity-only {@link optionsQuery} only supplies the filter chip universe. */
  protected readonly state = computed<KitAsyncStateValue>(() => asyncState(this.query, this.events().length === 0));

  /** The runner-id universe for the filter chips: the distinct runners in the
   * severity-scoped feed, plus the active runner so its chip never disappears, sorted.
   * Empty (row hidden) when there is at most one runner and none is selected — nothing
   * worth filtering.
   *
   * Falsy ids are stripped, mirroring the chunk side below: a projected escalation names
   * no runner, and an id-less row must not become a label-less chip whose `''` value
   * collides with the "All" chip's own reset sentinel. */
  protected readonly runnerIds = computed(() => eventRunnerIds(this.optionsQuery.data() ?? [], this.runnerId()));

  /** The chunk-id universe for the filter chips — same rule as {@link runnerIds}, over
   * the non-null `chunk_id`s (a runner-scoped event names no chunk). */
  protected readonly chunkIds = computed(() => eventChunkIds(this.optionsQuery.data() ?? [], this.chunkId()));

  protected onFilterChange(severity: string): void {
    this.severity.set(severity === '' ? null : narrowEventSeverity(severity));
  }

  protected onRunnerFilterChange(runnerId: string): void {
    this.runnerId.set(runnerId === '' ? null : runnerId);
  }

  protected onChunkFilterChange(chunkId: string): void {
    this.filters.patch({ chunk: chunkId === '' ? null : chunkId });
  }
}
