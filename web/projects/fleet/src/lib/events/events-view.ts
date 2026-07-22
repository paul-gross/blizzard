import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { EventView } from '../api/hub';
import { compactRef } from '../compact-ref';
import { KitBadge } from '../kit/kit-badge';
import { KitChips, type KitChipOption } from '../kit/kit-chips';
import { KitPanel } from '../kit/kit-panel';
import type { Tone } from '../kit/tone';
import { formatWhen } from '../when';

/** The severity filter row's options — `''` reads as "no filter" (every event). A
 * fixed closed set (unlike the runner/chunk axes, whose values are open and so are
 * derived from the feed's own ids — {@link EventsView.toOptions}). */
const SEVERITY_OPTIONS: readonly KitChipOption[] = [
  { value: '', label: 'All', testid: 'events-filter-all' },
  { value: 'info', label: 'Info', testid: 'events-filter-info' },
  { value: 'warning', label: 'Warning', testid: 'events-filter-warning' },
  { value: 'critical', label: 'Critical', testid: 'events-filter-critical' },
];

/** `EventView.severity` → {@link Tone} — critical reads as the board's alarm red,
 * warning as its live-work amber, and info as its dim/idle color, so the badge
 * agrees with the rest of the board's derived-status vocabulary rather than
 * inventing a severity-only color scale. */
const SEVERITY_TONE: Readonly<Record<string, Tone>> = {
  critical: 'stale',
  warning: 'running',
  info: 'idle',
};

/**
 * The Events tab's presentational half (blizzard#125 Phase 4) — the operational
 * event feed's row list, its severity/runner/chunk filter chips, and the
 * click-to-open chunk deep-link. Renders exactly the events and filter state it is
 * handed; injects no query of its own.
 *
 * The three filter axes match `GET /api/events`' own query params. Severity is a
 * fixed set ({@link SEVERITY_OPTIONS}); the runner and chunk axes are open, so the
 * container hands their id **universe** in (`runnerIds`/`chunkIds`) and this view
 * renders one chip per id — the universe is derived from a severity-only read, not
 * the filtered feed, so selecting a runner/chunk never makes the other chips vanish
 * (that derivation lives in `events-panel.ts`). An empty id array hides its row.
 *
 * Default sort is the server's (severity-then-recency, `GET /api/events`), so this
 * renders events as-received rather than re-sorting client-side.
 *
 * Every test handle here is `events-`prefixed, distinct from the in-rail Event log's
 * `event-log-*` handles (`event-log-panel.ts`) — two components on the same board
 * would otherwise make a browser test's locator ambiguous.
 */
@Component({
  selector: 'fleet-events-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitPanel, KitBadge, KitChips],
  template: `
    <fleet-kit-panel
      class="fill"
      aria-label="Events"
      data-testid="events-panel"
      label="Events · operational log"
      [count]="events().length || null"
      countTestid="events-count"
    >
      <div class="filters" data-testid="events-filters">
        <fleet-kit-chips [options]="severityOptions" [selectedValue]="severity() ?? ''" (choose)="onChoose($event)" />
        @if (runnerFilterOptions().length) {
          <fleet-kit-chips
            data-testid="events-runner-filter"
            [options]="runnerFilterOptions()"
            [selectedValue]="runner() ?? ''"
            (choose)="onRunnerChoose($event)"
          />
        }
        @if (chunkFilterOptions().length) {
          <fleet-kit-chips
            data-testid="events-chunk-filter"
            [options]="chunkFilterOptions()"
            [selectedValue]="chunk() ?? ''"
            (choose)="onChunkChoose($event)"
          />
        }
      </div>
      @if (loading()) {
        <p class="none" data-testid="events-loading">LOADING…</p>
      } @else if (error()) {
        <p class="none" data-testid="events-error">FAILED TO LOAD EVENTS</p>
      } @else if (events().length === 0) {
        <p class="none" data-testid="events-empty">NO EVENTS</p>
      } @else {
        <div class="rows" data-testid="events-rows">
          @for (ev of events(); track ev.id) {
            <div class="ev" data-testid="events-row" [attr.data-severity]="ev.severity">
              <fleet-kit-badge class="sev" [tone]="toneFor(ev.severity)" variant="pill" data-testid="events-severity">{{
                ev.severity
              }}</fleet-kit-badge>
              <span class="kind" data-testid="events-kind">{{ ev.kind }}</span>
              <span class="runner" data-testid="events-runner">{{ shortId(ev.runner_id) }}</span>
              <span class="msg" data-testid="events-message">{{ ev.message }}</span>
              <span class="time" data-testid="events-time">{{ formatWhen(ev.recorded_at) }}</span>
              @if (ev.chunk_id; as chunkId) {
                <button
                  type="button"
                  class="chunk"
                  data-testid="events-chunk"
                  [attr.aria-label]="'Open chunk ' + shortId(chunkId)"
                  (click)="selectChunk.emit(chunkId)"
                >
                  {{ shortId(chunkId) }}
                </button>
              }
              @if (ev.lease_id; as leaseId) {
                <span class="lease" data-testid="events-lease">{{ shortId(leaseId) }}</span>
              }
            </div>
          }
        </div>
      }
    </fleet-kit-panel>
  `,
  styles: `
    :host {
      display: flex;
      flex-direction: column;
      min-height: 0;
      flex: 1;
      font-family: var(--mono);
      font-size: var(--fs-base);
      font-variant-numeric: tabular-nums;
      color: var(--text);
    }
    fleet-kit-panel.fill {
      flex: 1;
    }
    .filters {
      display: flex;
      flex-direction: column;
      gap: 4px;
      padding: 6px 8px;
      border-bottom: 1px solid var(--line);
      flex: none;
    }
    .none {
      color: var(--label-dim);
      padding: 10px 8px;
      margin: 0;
      font-size: var(--fs-sm);
      letter-spacing: 0.08em;
    }
    .rows {
      overflow-y: auto;
      min-height: 0;
      flex: 1;
    }
    .ev {
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 6px;
      padding: 4px 8px;
      border-bottom: 1px solid var(--line);
      font-size: var(--fs-sm);
      line-height: 1.5;
    }
    .kind {
      color: var(--cyan);
    }
    .runner {
      color: var(--label-dim);
      font-size: var(--fs-xs);
      white-space: nowrap;
    }
    .msg {
      color: var(--text);
      overflow-wrap: anywhere;
      flex: 1;
    }
    .time {
      color: var(--label-dim);
      font-size: var(--fs-xs);
      white-space: nowrap;
    }
    .chunk {
      font-family: inherit;
      font-size: var(--fs-xs);
      color: var(--amber-hi);
      background: transparent;
      border: 1px solid var(--line);
      cursor: pointer;
      padding: 0 4px;
    }
    .chunk:hover {
      border-color: var(--cyan);
    }
    .lease {
      color: var(--label-dim);
      font-size: var(--fs-xs);
    }
  `,
})
export class EventsView {
  /** The event feed to render, in the order given (server-sorted). */
  readonly events = input.required<readonly EventView[]>();

  /** The active severity filter, or `null` for "all" — highlights the matching chip. */
  readonly severity = input<string | null>(null);

  /** The active runner filter, or `null` for "all". */
  readonly runner = input<string | null>(null);

  /** The active chunk filter, or `null` for "all". */
  readonly chunk = input<string | null>(null);

  /** The runner-id universe for the runner filter chips (the container derives it from a
   * severity-only read so it stays stable under a runner/chunk selection). Empty hides the
   * runner filter row. */
  readonly runnerIds = input<readonly string[]>([]);

  /** The chunk-id universe for the chunk filter chips — same contract as {@link runnerIds}. */
  readonly chunkIds = input<readonly string[]>([]);

  /** Whether the feed's first read is still in flight. */
  readonly loading = input(false);

  /** Whether the feed's read failed. */
  readonly error = input(false);

  /** Emitted with a chunk id when its row's chunk button is activated. */
  readonly selectChunk = output<string>();

  /** Emitted with the chosen severity filter (`''` for "all", handed through as-is —
   * the container maps it to `null`). */
  readonly filterChange = output<string>();

  /** Emitted with the chosen runner filter (`''` for "all"). */
  readonly runnerFilterChange = output<string>();

  /** Emitted with the chosen chunk filter (`''` for "all"). */
  readonly chunkFilterChange = output<string>();

  protected readonly severityOptions = SEVERITY_OPTIONS;

  /** The runner filter chips — an "All" option plus one per id in {@link runnerIds},
   * compact-ref labelled. Empty when the container handed no ids (nothing to filter). */
  protected readonly runnerFilterOptions = computed(() =>
    EventsView.toOptions(this.runnerIds(), 'events-runner-filter'),
  );

  /** The chunk filter chips — same shape as {@link runnerFilterOptions}. */
  protected readonly chunkFilterOptions = computed(() =>
    EventsView.toOptions(this.chunkIds(), 'events-chunk-filter'),
  );

  /** Build a chip row from an id universe: an "All" reset plus one chip per id, keyed
   * by the raw id (unique testid) and labelled with its compact ref. `[]` in → `[]` out,
   * so the row hides when there is nothing to filter. */
  private static toOptions(ids: readonly string[], testidPrefix: string): readonly KitChipOption[] {
    if (ids.length === 0) return [];
    return [
      { value: '', label: 'All', testid: `${testidPrefix}-all` },
      ...ids.map((id) => ({ value: id, label: compactRef(id), testid: `${testidPrefix}-${id}` })),
    ];
  }

  protected toneFor(severity: string): Tone {
    return SEVERITY_TONE[severity] ?? 'idle';
  }

  protected shortId(id: string): string {
    return compactRef(id);
  }

  protected formatWhen(iso: string): string {
    return formatWhen(iso);
  }

  protected onChoose(value: string): void {
    this.filterChange.emit(value);
  }

  protected onRunnerChoose(value: string): void {
    this.runnerFilterChange.emit(value);
  }

  protected onChunkChoose(value: string): void {
    this.chunkFilterChange.emit(value);
  }
}
