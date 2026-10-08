import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { asyncState, injectChunkUrlSelection, type runnerApi, ViewportService } from 'fleet';

import type { MachineChunkStatus } from './chunk-list/chunk-status';
import { injectRunnerLeasesQuery } from '../core/leases.query';
import {
  activeLeases,
  chunkLeasesFor,
  chunksEmptyText,
  chunkStatusFor,
  escalationFor,
  type MachineChunkRow,
  machineChunkRows,
  visibleChunkRows,
} from './app-panel.model';
import { LocalPanelLayout } from './app-panel-layout';
import { LocalPanelMobile } from './app-panel-mobile';
import { injectRunnerDashboardQuery } from '../core/status.query';

/**
 * The runner's machine-local panel — the data-orchestration container.
 * Owns the leases query plus the one shared
 * {@link injectRunnerDashboardQuery} (the composed `GET
 * /api/dashboard` read, folding what were five separate query injections
 * here), the one derived-status fold ({@link machineChunkRows}), and
 * the selection — which chunk is open, bound to the URL's `?chunk=` query
 * param so a link is shareable and a reload keeps its place.
 * Every panel below it (via {@link LocalPanelLayout}) is presentational or
 * owns just its own read.
 *
 * The fold and the selection stay here rather than in the layout, per the
 * epic's design decision: the layout takes `machineChunks`/`selected*` as
 * plain inputs, so it is testable without a runner-client stub. The URL is the
 * single source of truth — the panel derives its selection from the query params
 * and every click writes them back, never the reverse.
 *
 * Owns no header state: the desktop header and the mobile titlebar mount at
 * the app root (`../../runner/src/app/shell/nav/app-header.ts`,
 * `../../runner/src/app/shell/nav/mobile-titlebar.ts`) and inject
 * {@link injectRunnerDashboardQuery} themselves rather than reading it off
 * this container — TanStack dedupes the extra injection, so it costs no
 * extra request.
 */
@Component({
  selector: 'app-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [LocalPanelLayout, LocalPanelMobile],
  templateUrl: './app-panel.html',
  styleUrl: './app-panel.css',
})
export class LocalPanel {
  /** The page-level shell picker (`../docs/designs/mobile/README.md`'s
   * "adaptive shells over shared guts") — desktop renders the existing
   * three-column {@link LocalPanelLayout} unchanged; mobile renders
   * {@link LocalPanelMobile} instead, `@defer`-loaded so the desktop bundle
   * doesn't carry it. Owns neither the mobile bottom tab bar nor the viewport
   * override; see `../shell/nav/`. */
  protected readonly viewport = inject(ViewportService);

  protected readonly mode = this.viewport.mode;

  protected readonly leasesQuery = injectRunnerLeasesQuery();

  /** The panel's whole machine-local status read — `GET
   * /api/dashboard`, the same 5s-polled query every other rail on this panel
   * injects; TanStack dedupes the N injections into one request. */
  protected readonly dashboardQuery = injectRunnerDashboardQuery();

  /** The active + recently-closed leases, server-ordered; empty until the first read resolves. */
  private readonly leases = computed(() => this.leasesQuery.data() ?? []);

  /**
   * The liveness rail shows *active* leases only — a closed lease is history,
   * carried by {@link machineChunks} as its chunk's newest attempt instead.
   */
  protected readonly activeLeases = computed(() => activeLeases(this.leases()));

  /** The leases rail's async triad state — loading/error take precedence, then
   * no active leases, else the agent rows render. */
  protected readonly leasesTriadState = computed(() => asyncState(this.leasesQuery, this.activeLeases().length === 0));

  /** The mobile chunks pane's async triad state — mobile renders the
   * unfiltered {@link machineChunks} (left mobile's own filter out
   * of scope), so this reads that list's emptiness, sharing the leases
   * query's loading/error state. */
  protected readonly chunksTriadState = computed(() => asyncState(this.leasesQuery, this.machineChunks().length === 0));

  /** The desktop chunks pane's own triad state — derived from {@link visibleChunks},
   * the filtered list {@link LocalPanelLayout} renders, not the unfiltered
   * {@link machineChunks} the shared {@link chunksTriadState} above reads. Keeps
   * "ready" and "has rows to show" in sync when the filter hides everything. */
  protected readonly visibleChunksTriadState = computed(() => asyncState(this.leasesQuery, this.visibleChunks().length === 0));

  /** The desktop chunks pane's empty-state text — distinguishes "nothing on this
   * machine" from "the filter hid everything", naming the hidden count so the
   * operator knows to check "show all". */
  protected readonly chunksEmptyText = computed<string>(() =>
    chunksEmptyText(this.machineChunks().length, this.visibleChunks().length),
  );

  /**
   * One row per chunk on this machine: the chunk's newest lease (the server
   * orders actives first, then the recent-closed block, so the first lease
   * seen per `chunk_id` is the freshest attempt) plus every attempt of the
   * chunk and the derived status — folded once here, handed to the row and the
   * detail dock alike. Each row's `leases` is ordered oldest → newest, so
   * `lease` (the summary/status subject) is that list's own newest entry.
   */
  protected readonly machineChunks = computed<MachineChunkRow[]>(() =>
    machineChunkRows(this.leases(), this.dashboardQuery.data()),
  );

  /** The chunks list's "show all" filter state — plain UI state,
   * unchecked by default. Client-side only: narrows what {@link visibleChunks}
   * renders, never the server-side `RECENT_LEASE_LIMIT`-bounded `/api/leases` read. */
  protected readonly showAllChunks = signal(false);

  /** The chunks list's visible rows — {@link machineChunks} itself when
   * {@link showAllChunks} is checked, else rows whose derived
   * {@link MachineChunkStatus.tone} isn't `done`/`idle`. Filters on the
   * *derived* status (not raw lease state), so a closed lease with an open
   * escalation still shows as `NEEDS HUMAN`. Selection stays keyed off the
   * unfiltered {@link machineChunks}, so a hidden chunk is still deep-linkable.
   * Desktop-only — {@link LocalPanelMobile} takes the unfiltered
   * list directly; mobile's own filter is out of scope here. */
  protected readonly visibleChunks = computed<MachineChunkRow[]>(() =>
    visibleChunkRows(this.machineChunks(), this.showAllChunks()),
  );

  /** The open-ask count for the asks panel's header note. */
  protected readonly openAskCount = computed(() => (this.dashboardQuery.data()?.asks?.items ?? []).length);

  /** What is open in the panel, held in the URL's `?chunk=` via the shared
   * {@link injectChunkUrlSelection} — the router coupling lives there, not
   * here. Carries no `attempt` selection: this panel neither reads nor
   * clears an `attempt` query param, and writes none itself. */
  private readonly selection = injectChunkUrlSelection();

  /**
   * The `chunk_id` currently selected. A lease row selects its chunk too
   * ({@link selectLease}) — the lease rail and the chunks list share one
   * selection, reflected on both.
   */
  protected readonly selectedChunkId = this.selection.chunkId;

  /** Write a chunk selection to the URL, clearing any stale `attempt` (attempt
   * lease ids are chunk-specific, so a new chunk defaults to its newest). */
  protected selectChunk(chunkId: string): void {
    this.selection.select(chunkId);
  }

  /** Selecting a lease row selects its chunk — the shared selection both rails
   * reflect; the detail dock defaults to the chunk's newest attempt. */
  protected selectLease(leaseId: string): void {
    const lease = this.leases().find((candidate) => candidate.lease_id === leaseId);
    if (lease) this.selection.select(lease.chunk_id);
  }

  /** Clear the selection entirely — the mobile shell's back affordance, which
   * closes its drill-down by removing what the detail screen renders off. A
   * plain navigation like any other selection write, so the device back button
   * and this button walk the same history. Desktop has no caller: its dock
   * simply falls back to `SELECT A CHUNK`. */
  protected clearSelection(): void {
    this.selection.select(null);
  }

  /**
   * The selected chunk's attempts (oldest → newest) — what the detail dock's
   * summary/status renders off the newest. Empty when nothing is selected.
   */
  protected readonly selectedChunkLeases = computed<readonly runnerApi.LeaseView[]>(() =>
    chunkLeasesFor(this.machineChunks(), this.selectedChunkId()),
  );

  protected readonly selectedStatus = computed<MachineChunkStatus | null>(() =>
    chunkStatusFor(this.machineChunks(), this.selectedChunkId()),
  );

  /** The open escalation for the selected chunk, when one exists — carries the resume command. */
  protected readonly selectedEscalation = computed<runnerApi.EscalationView | null>(() =>
    escalationFor(this.dashboardQuery.data(), this.selectedChunkId()),
  );
}
