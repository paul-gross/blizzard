import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { type KitAsyncStateValue, asyncState, injectNowSignal, injectRunnerHostNames, ViewportService } from 'fleet';
import { injectQueryFilters } from '../core/route-state';
import { injectHubHostsQuery } from '../core/hosts.query';
import { EventsView } from './events-view';
import { eventChunkIds, eventRunnerFleetIds, eventRunnerIds, eventRunnerNames } from './events-panel.model';
import { injectHubEventsQuery, narrowEventSeverity } from './events.query';

/**
 * The Events tab's **container** — the board's operational
 * event feed (`GET /api/events`), filterable by severity/runner/chunk.
 *
 * Owns the severity/runner filter state as signals, the chunk filter in the URL's
 * `?chunk=` (so a chunk's own page can deep-link its events, and the filtered view
 * survives a reload), and the reactive query over them, and renders the presentational {@link EventsView}
 * (freshness: {@link injectHubEventsQuery}).
 *
 * The runner and chunk chip **universe** (`runnerIds`/`chunkIds`) comes from a
 * second, **severity-only** read (`optionsQuery`), not narrowed by the runner/chunk
 * selection.
 */
@Component({
  selector: 'app-events-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [EventsView],
  templateUrl: './events-panel.html',
  styleUrl: './events-panel.css',
})
export class EventsPanel {
  /** The active severity filter, or `null` for "every severity". */
  protected readonly severity = computed(() => narrowEventSeverity(this.filters.read('severity') ?? ''));
  /** The active runner filter, or `null` for "every runner". */
  protected readonly runnerId = computed(() => this.filters.read('runner'));
  private readonly filters = injectQueryFilters();
  protected readonly runnerHosts = injectRunnerHostNames();
  protected readonly now = injectNowSignal();
  protected readonly viewport = inject(ViewportService);
  private readonly hostsQuery = injectHubHostsQuery();
  protected readonly runnerFleetIds = computed(() => eventRunnerFleetIds(this.hostsQuery.data() ?? []));

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

  /** The runner-id universe for the dropdown: the distinct runners in the
   * severity-scoped feed, plus the active runner so its option never disappears, sorted.
   *
   * Falsy ids are stripped, mirroring the chunk side below: a projected escalation names
   * no runner, and an id-less row must not become a label-less chip whose `''` value
   * collides with the "All" chip's own reset sentinel. */
  protected readonly runnerIds = computed(() => eventRunnerIds(this.optionsQuery.data() ?? [], this.runnerId()));
  /** The runner options' names, from the same options read. */
  protected readonly runnerNames = computed(() => eventRunnerNames(this.optionsQuery.data() ?? []));

  /** The chunk-id universe for the filter chips — same rule as {@link runnerIds}, over
   * the non-null `chunk_id`s (a runner-scoped event names no chunk). */
  protected readonly chunkIds = computed(() => eventChunkIds(this.optionsQuery.data() ?? [], this.chunkId()));

  protected onFilterChange(severity: string): void {
    this.filters.patch({ severity: severity || null });
  }

  protected onRunnerFilterChange(runnerId: string): void {
    this.filters.patch({ runner: runnerId || null });
  }

  protected onChunkFilterChange(chunkId: string): void {
    this.filters.patch({ chunk: chunkId === '' ? null : chunkId });
  }
}
