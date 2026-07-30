import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { EventView } from '../api/hub';
import { compactRef } from '../compact-ref';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitBadge } from '../kit/kit-badge';
import { KitChips, type KitChipOption } from '../kit/kit-chips';
import { KitPanel } from '../kit/kit-panel';
import type { Tone } from '../kit/tone';
import { FleetWhen } from '../when-display';

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
 * Each row is a **time-first grid** — time, chunk, severity, kind, runner, message,
 * lease — following the in-rail Event log's leading dim-stamp column
 * (`event-log-panel.ts`) rather than the wrapping flex line it used to be, so a
 * reader scans the feed down its timestamps instead of hunting for one per row.
 *
 * Every test handle here is `events-`prefixed, distinct from the in-rail Event log's
 * `event-log-*` handles (`event-log-panel.ts`) — two components on the same board
 * would otherwise make a browser test's locator ambiguous.
 */
@Component({
  selector: 'fleet-events-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitPanel, KitBadge, KitChips, FleetWhen],
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
      <fleet-kit-async-state
        [state]="state()"
        loadingText="LOADING…"
        loadingTestid="events-loading"
        errorText="FAILED TO LOAD EVENTS"
        errorTestid="events-error"
        emptyText="NO EVENTS"
        emptyTestid="events-empty"
      >
        <div class="rows" data-testid="events-rows">
          @for (ev of events(); track ev.id) {
            <div class="ev" data-testid="events-row" [attr.data-severity]="ev.severity">
              <fleet-when class="time" data-testid="events-time" [iso]="ev.recorded_at" />
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
              } @else {
                <span class="chunk-none" aria-hidden="true">—</span>
              }
              <fleet-kit-badge class="sev" [tone]="toneFor(ev.severity)" variant="soft" data-testid="events-severity">{{
                ev.severity
              }}</fleet-kit-badge>
              <span class="kind" data-testid="events-kind" [title]="ev.kind">{{ ev.kind }}</span>
              @if (ev.runner_id; as runnerId) {
                <span class="runner" data-testid="events-runner" [title]="runnerId">{{ shortId(runnerId) }}</span>
              } @else {
                <span class="runner" data-testid="events-runner" aria-hidden="true">—</span>
              }
              <span class="msg" data-testid="events-message">{{ ev.message }}</span>
              @if (ev.lease_id; as leaseId) {
                <span class="lease" data-testid="events-lease">{{ shortId(leaseId) }}</span>
              }
            </div>
          }
        </div>
      </fleet-kit-async-state>
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
    .rows {
      overflow-y: auto;
      min-height: 0;
      flex: 1;
    }
    /* Time-first columns, matching the in-rail Event log's leading dim stamp
       (event-log-panel.ts). Every metadata track is FIXED, not content-sized:
       each row is its own grid container, so only a fixed track aligns down the
       feed — a content-sized one re-measures per row and the column wanders. That
       covers the whole metadata block (time, chunk, severity, kind, runner), so
       the message starts at one x on every row; the message takes the rest and the
       lease trails.

       Tracks are ch-based, so they scale with whatever face --mono resolves to on
       the platform (tokens.css offers four) rather than assuming one face's advance
       width. A ch here measures against the row's own --fs-sm, while the time and
       runner cells render one step down at --fs-xs, so those two carry ~8% slack
       for free. Kind and runner are open sets, so each is sized for its longest
       known value and ellipsized past it — the full string stays in the title
       attribute, so a truncation costs nothing. */
    .ev {
      display: grid;
      grid-template-columns: 15ch 8ch 10ch 18ch 12ch 1fr auto;
      align-items: baseline;
      gap: 6px;
      padding: 4px 8px;
      border-bottom: 1px solid var(--line);
      font-size: var(--fs-sm);
      line-height: 1.5;
    }
    .kind {
      color: var(--cyan);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .runner {
      color: var(--label-dim);
      font-size: var(--fs-xs);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .msg {
      color: var(--text);
      overflow-wrap: anywhere;
      min-width: 0;
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
      justify-self: start;
    }
    .chunk:hover {
      border-color: var(--cyan);
    }
    /* A chunk-less (runner-scoped) row still occupies the chunk column, so the
       columns after it stay aligned with the rows that do name a chunk. The inset
       matches the sibling .chunk button's own border + padding, so the dash sits
       where that button's text sits rather than 5px to its left. */
    .chunk-none {
      color: var(--label-dim);
      font-size: var(--fs-xs);
      justify-self: start;
      padding: 0 5px;
    }
    .sev {
      justify-self: start;
    }
    .lease {
      color: var(--label-dim);
      font-size: var(--fs-xs);
    }
    /* Below the board's own mobile cutoff the fixed metadata tracks no longer fit:
       they alone are wider than a handset's content box, so the message track
       would resolve to 0 and — with overflow-wrap: anywhere — wrap one character
       per line, turning every row into a ribbon hundreds of pixels tall.

       So a narrow viewport drops back to the wrapping flex line the row used
       before the grid, with the message on its own full-width line beneath the
       metadata. Flex cannot collapse a track to nothing, so this degrades at any
       width rather than only at the ones someone thought to measure. Nothing is
       lost: cross-row alignment buys a reader a column to scan down, and a phone
       is one column wide already.

       This forks on a media query rather than on ViewportService.mode(), which is
       how the app picks a *shell* (matches-mobile-viewport.ts). The two answer
       different questions: mode() includes a manual override, so a user pinning
       mobile on a wide monitor still has room for the grid and should keep it,
       while a narrow *desktop* window has no room and still needs the fallback.
       What breaks the layout is available width, so width is what it keys on —
       at the same 767.98px cutoff ViewportService uses, so the two agree wherever
       both apply. */
    @media (max-width: 767.98px) {
      .ev {
        display: flex;
        flex-wrap: wrap;
      }
      .msg {
        flex: 1 1 100%;
      }
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

  /** The feed's async state (AC 5) — loading/error withhold the empty copy
   * until the read resolves. */
  readonly state = input.required<KitAsyncStateValue>();

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
