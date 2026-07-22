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
  selector: 'fleet-events-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [EventsView],
  template: `
    <fleet-events-view
      [events]="events()"
      [severity]="severity()"
      [runner]="runnerId()"
      [chunk]="chunkId()"
      [runnerIds]="runnerIds()"
      [chunkIds]="chunkIds()"
      [loading]="query.isPending()"
      [error]="query.isError()"
      (selectChunk)="selectChunk.emit($event)"
      (filterChange)="onFilterChange($event)"
      (runnerFilterChange)="onRunnerFilterChange($event)"
      (chunkFilterChange)="onChunkFilterChange($event)"
    />
  `,
})
export class EventsPanel {
  /** Emitted with a chunk id when a row's chunk deep-link is activated. */
  readonly selectChunk = output<string>();

  /** The active severity filter, or `null` for "every severity". */
  protected readonly severity = signal<string | null>(null);
  /** The active runner filter, or `null` for "every runner". */
  protected readonly runnerId = signal<string | null>(null);
  /** The active chunk filter, or `null` for "every chunk". */
  protected readonly chunkId = signal<string | null>(null);

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

  /** The runner-id universe for the filter chips: the distinct runners in the
   * severity-scoped feed, plus the active runner so its chip never disappears, sorted.
   * Empty (row hidden) when there is at most one runner and none is selected — nothing
   * worth filtering. */
  protected readonly runnerIds = computed(() =>
    this.filterUniverse(
      (this.optionsQuery.data() ?? []).map((e) => e.runner_id),
      this.runnerId(),
    ),
  );

  /** The chunk-id universe for the filter chips — same rule as {@link runnerIds}, over
   * the non-null `chunk_id`s (a runner-scoped event names no chunk). */
  protected readonly chunkIds = computed(() =>
    this.filterUniverse(
      (this.optionsQuery.data() ?? []).map((e) => e.chunk_id).filter((c): c is string => !!c),
      this.chunkId(),
    ),
  );

  /** Distinct ids ∪ the active selection, sorted — or `[]` when there is nothing worth
   * filtering (≤1 distinct id and no active selection), which hides the chip row. */
  private filterUniverse(ids: readonly string[], active: string | null): readonly string[] {
    const distinct = new Set(ids);
    if (active) distinct.add(active);
    if (distinct.size < 2 && active === null) return [];
    return [...distinct].sort();
  }

  protected onFilterChange(severity: string): void {
    this.severity.set(severity === '' ? null : severity);
  }

  protected onRunnerFilterChange(runnerId: string): void {
    this.runnerId.set(runnerId === '' ? null : runnerId);
  }

  protected onChunkFilterChange(chunkId: string): void {
    this.chunkId.set(chunkId === '' ? null : chunkId);
  }
}
