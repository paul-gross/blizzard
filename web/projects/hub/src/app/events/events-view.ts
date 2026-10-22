import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { type EventView, hubApi, compactRef, runnerDisplayName, runnerTitle, KitAsyncState, type KitAsyncStateValue, KitBadge, KitButton, KitChips, type KitChipOption, KitSelect, KitPanel, openLookup, type Tone, FleetWhen } from 'fleet';

/** Each severity's chip label; a severity this build does not know reads raw. */
const SEVERITY_LABEL = openLookup<hubApi.EventLogSeverity, string>(
  { info: 'Info', warning: 'Warning', critical: 'Critical' },
  (raw) => raw,
);

/** The severity filter row's options — `''` reads as "no filter" (every event). A
 * fixed closed set (unlike the runner/chunk axes, whose values are open and so are
 * derived from the feed's own ids — {@link EventsView.toOptions}). The chips read in
 * ascending severity, the generated const's order reversed. */
const SEVERITY_OPTIONS: readonly KitChipOption[] = [
  { value: '', label: 'All', testid: 'events-filter-all' },
  ...[...Object.values(hubApi.EventLogSeverity)].reverse().map((severity) => ({
    value: severity,
    label: SEVERITY_LABEL(severity),
    testid: `events-filter-${severity}`,
  })),
];

/** `EventView.severity` → {@link Tone} — critical reads as the board's alarm red,
 * warning as its live-work amber, and info as its dim/idle color, so the badge
 * agrees with the rest of the board's derived-status vocabulary rather than
 * inventing a severity-only color scale. A severity this build does not know reads idle. */
const SEVERITY_TONE = openLookup<EventView['severity'], Tone>({ critical: 'stale', warning: 'running', info: 'idle' }, () => 'idle');

/**
 * The Events tab's presentational half — the operational
 * event feed's row list, its severity/runner/chunk filter chips, and the
 * click-to-open chunk deep-link. Renders exactly the events and filter state it is
 * handed; injects no query of its own.
 *
 * The three filter axes match `GET /api/events`' own query params. Severity is a
 * fixed set ({@link SEVERITY_OPTIONS}); the runner and chunk axes are open, so the
 * container hands their id **universe** in (`runnerIds`/`chunkIds`) and this view
 * renders one chip per id. An empty id array hides its row.
 *
 * Events render as-received. Each row is a **time-first grid** — time, chunk,
 * severity, kind, runner, message, lease. Every test handle is `events-`prefixed.
 */
@Component({
  selector: 'app-events-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitPanel, KitBadge, KitButton, KitChips, KitSelect, FleetWhen],
  templateUrl: './events-view.html',
  styleUrl: './events-view.css',
})
export class EventsView {
  /** The event feed to render, in the order given (server-sorted). */
  readonly events = input.required<readonly EventView[]>();

  /** Each runner's host name by runner id — a runner the map omits shows no host. */
  readonly runnerHosts = input<ReadonlyMap<string, string>>(new Map());

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
  /** Each runner's name by id, for the runner chips' display names; an id absent here shows its compact ref. */
  readonly runnerNames = input<ReadonlyMap<string, string>>(new Map());

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
   * labelled with the runner's display name. Empty when the container handed no ids (nothing to filter). */
  protected readonly runnerFilterOptions = computed(() =>
    EventsView.toOptions(this.runnerIds(), 'events-runner-filter', (id) =>
      runnerDisplayName(id, this.runnerNames().get(id), this.runnerHosts().get(id)),
    ),
  );

  /** The chunk filter chips — same shape as {@link runnerFilterOptions}. */
  protected readonly chunkFilterOptions = computed(() =>
    EventsView.toOptions(this.chunkIds(), 'events-chunk-filter', compactRef),
  );

  /** Build a chip row from an id universe: an "All" reset plus one chip per id, keyed
   * by the raw id (unique testid) and labelled by `label`. `[]` in → `[]` out,
   * so the row hides when there is nothing to filter. */
  private static toOptions(
    ids: readonly string[],
    testidPrefix: string,
    label: (id: string) => string,
  ): readonly KitChipOption[] {
    if (ids.length === 0) return [];
    return [
      { value: '', label: 'All', testid: `${testidPrefix}-all` },
      ...ids.map((id) => ({ value: id, label: label(id), testid: `${testidPrefix}-${id}` })),
    ];
  }

  protected toneFor(severity: EventView['severity']): Tone {
    return SEVERITY_TONE(severity);
  }

  protected shortId(id: string): string {
    return compactRef(id);
  }

  protected runnerName(runnerId: string, name: string | null | undefined): string {
    return runnerDisplayName(runnerId, name, this.runnerHosts().get(runnerId));
  }

  /** The runner cell's tooltip — the display name the cell may ellipsize, then the full id it compacts. */
  protected runnerTitle(runnerId: string, name: string | null | undefined): string {
    return runnerTitle(runnerId, name, this.runnerHosts().get(runnerId));
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
