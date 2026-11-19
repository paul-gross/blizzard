import { ChangeDetectionStrategy, Component, computed, input, output, resource } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AppLink } from 'fleet/shell';

import { fleetIdOf, type HostMembership } from '../core/runners.domain';

import { type EventView, hubApi, compactRef, formatSeenTag, runnerDisplayName, runnerTitle, KitAsyncState, type KitAsyncStateValue, type KitChipOption, KitSelect, KitSkeleton, openLookup } from 'fleet';
import { eventFingerprints } from './event-detail.model';

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
  { value: '', label: 'Severity', testid: 'events-filter-all' },
  ...[...Object.values(hubApi.EventLogSeverity)].reverse().map((severity) => ({
    value: severity,
    label: SEVERITY_LABEL(severity),
    testid: `events-filter-${severity}`,
  })),
];

const SEVERITY_ICON = openLookup<EventView['severity'], string>(
  { critical: 'critical', warning: 'warning', info: 'info' }, () => 'unknown',
);

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
 * Events render as-received as compact cards; selecting one opens its detail page.
 */
@Component({
  selector: 'app-events-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitSelect, KitSkeleton, AppLink, RouterLink],
  templateUrl: './events-view.html',
  styleUrl: './events-view.css',
})
export class EventsView {
  /** The event feed to render, in the order given (server-sorted). */
  readonly events = input.required<readonly EventView[]>();
  private readonly identities = resource({
    params: () => this.events(),
    loader: ({ params }) => eventFingerprints(params),
  });
  protected fingerprint(event: EventView): string | null {
    return this.identities.value()?.get(event) ?? null;
  }

  /** Each runner's host name by runner id — a runner the map omits shows no host. */
  readonly runnerHosts = input<ReadonlyMap<string, string>>(new Map());
  /** The hosts and the runners each lists — what a runner's Fleet link resolves against. */
  readonly hosts = input<readonly HostMembership[]>([]);
  readonly now = input.required<number>();
  readonly mobile = input(false);
  protected readonly skeletonRows = [0, 1, 2, 3, 4];

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
    EventsView.toOptions(this.runnerIds(), 'events-runner-filter', 'Runners', (id) =>
      runnerDisplayName(id, this.runnerNames().get(id), this.runnerHosts().get(id)),
    ),
  );

  /** The chunk filter chips — same shape as {@link runnerFilterOptions}. */
  protected readonly chunkFilterOptions = computed(() =>
    EventsView.toOptions(this.chunkIds(), 'events-chunk-filter', 'Chunks', compactRef),
  );

  /** Build dropdown options from an id universe, including an "All" reset. */
  private static toOptions(
    ids: readonly string[],
    testidPrefix: string,
    allLabel: string,
    label: (id: string) => string,
  ): readonly KitChipOption[] {
    return [
      { value: '', label: allLabel, testid: `${testidPrefix}-all` },
      ...ids.map((id) => ({ value: id, label: label(id), testid: `${testidPrefix}-${id}` })),
    ];
  }

  protected iconFor(severity: EventView['severity']): string { return SEVERITY_ICON(severity); }

  protected age(recordedAt: string): string { return formatSeenTag(recordedAt, this.now()); }

  protected fleetId(runnerId: string): string { return fleetIdOf(runnerId, this.hosts()); }

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
