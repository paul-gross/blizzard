import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { injectChunkUrlSelection, type KitAsyncStateValue, type runnerApi, ViewportService } from 'fleet';

import { type MachineChunkStatus, deriveMachineChunkStatus } from './chunk-status';
import { injectRunnerLeasesQuery } from './leases.query';
import { LocalPanelLayout } from './local-panel-layout';
import { LocalPanelMobile } from './local-panel-mobile';
import { injectRunnerDashboardQuery } from './status.query';

/** One row in the machine-chunks list: a chunk's newest lease plus its derived
 * machine-side status, pre-folded so the layout needs no second read. `leases`
 * carries *every* attempt of the chunk (oldest → newest) — the detail dock
 * resolves its own summary off the newest entry without a second read of its
 * own; `lease` is that same newest entry, already resolved for the row. */
export interface MachineChunkRow {
  readonly lease: runnerApi.LeaseView;
  readonly leases: readonly runnerApi.LeaseView[];
  readonly status: MachineChunkStatus;
}

/**
 * The runner's machine-local panel — the data-orchestration container
 * (issue #80). Owns the leases query plus the one shared
 * {@link injectRunnerDashboardQuery} (issue #311's composed `GET
 * /api/dashboard` read, folding what were five separate query injections
 * here), the one derived-status fold ({@link deriveMachineChunkStatus}), and
 * the selection — which chunk is open, bound to the URL's `?chunk=` query
 * param so a link is shareable and a reload keeps its place (issue #99).
 * Every panel below it (via {@link LocalPanelLayout}) is presentational or
 * owns just its own read.
 *
 * The fold and the selection stay here rather than in the layout, per the
 * epic's design decision: the layout takes `machineChunks`/`selected*` as
 * plain inputs, so it is testable without a runner-client stub. The URL is the
 * single source of truth — the panel derives its selection from the query params
 * and every click writes them back, never the reverse.
 *
 * Owns no header state (issue #325): the shared header's connection cell and
 * live stat cells used to be folded here and threaded down as inputs to
 * {@link LocalPanelLayout}. Both the desktop header and the mobile titlebar
 * moved to the app root (`../../runner/src/app/nav/app-header.ts`,
 * `../../runner/src/app/nav/mobile-titlebar.ts`), so they now inject
 * {@link injectRunnerDashboardQuery} themselves rather than reading it off
 * this container — TanStack dedupes the extra injection, so it costs no
 * extra request.
 */
@Component({
  selector: 'local-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [LocalPanelLayout, LocalPanelMobile],
  templateUrl: './local-panel.html',
  styleUrl: './local-panel.css',
})
export class LocalPanel {
  /** The page-level shell picker (`../docs/designs/mobile/README.md`'s
   * "adaptive shells over shared guts") — desktop renders the existing
   * three-column {@link LocalPanelLayout} unchanged; mobile renders
   * {@link LocalPanelMobile} instead, `@defer`-loaded so the desktop bundle
   * doesn't carry it. The persistent mobile bottom tab bar lives at the app
   * root now (issue #313, `../../runner/src/app/nav/mobile-tab-bar.ts`), not
   * here, so it survives navigating to `/events` where this component isn't
   * mounted at all. The viewport override itself lives behind each shell's
   * own header menu (`KitMenu`, mobile polish feedback item 5) — the app
   * root's `AppHeader` and `MobileTitlebar` (issue #325), not this component
   * — rather than an always-visible strip above both. */
  protected readonly viewport = inject(ViewportService);

  protected readonly mode = this.viewport.mode;

  protected readonly leasesQuery = injectRunnerLeasesQuery();

  /** The panel's whole machine-local status read (issue #311) — `GET
   * /api/dashboard`, the same 5s-polled query every other rail on this panel
   * injects; TanStack dedupes the N injections into one request. Replaces
   * what were five separate query injections here (asks, escalations,
   * takeovers, status, environments). */
  protected readonly dashboardQuery = injectRunnerDashboardQuery();

  /** The active + recently-closed leases, server-ordered; empty until the first read resolves. */
  private readonly leases = computed(() => this.leasesQuery.data() ?? []);

  /**
   * The liveness rail shows *active* leases only — a closed lease is history,
   * carried by {@link machineChunks} as its chunk's newest attempt instead.
   */
  protected readonly activeLeases = computed(() => this.leases().filter((lease) => lease.state !== 'closed'));

  /** The leases rail's async triad state — loading/error take precedence, then
   * no active leases, else the agent rows render. */
  protected readonly leasesTriadState = computed<KitAsyncStateValue>(() => {
    if (this.leasesQuery.isPending()) return 'loading';
    if (this.leasesQuery.isError()) return 'error';
    return this.activeLeases().length === 0 ? 'empty' : 'ready';
  });

  /** The mobile chunks pane's async triad state — mobile renders the
   * unfiltered {@link machineChunks} (issue #134 left mobile's own filter out
   * of scope), so this reads that list's emptiness, sharing the leases
   * query's loading/error state. */
  protected readonly chunksTriadState = computed<KitAsyncStateValue>(() => {
    if (this.leasesQuery.isPending()) return 'loading';
    if (this.leasesQuery.isError()) return 'error';
    return this.machineChunks().length === 0 ? 'empty' : 'ready';
  });

  /** The desktop chunks pane's own triad state — derived from {@link visibleChunks},
   * the filtered list {@link LocalPanelLayout} renders, not the unfiltered
   * {@link machineChunks} the shared {@link chunksTriadState} above reads. Keeps
   * "ready" and "has rows to show" in sync when the filter hides everything. */
  protected readonly visibleChunksTriadState = computed<KitAsyncStateValue>(() => {
    if (this.leasesQuery.isPending()) return 'loading';
    if (this.leasesQuery.isError()) return 'error';
    return this.visibleChunks().length === 0 ? 'empty' : 'ready';
  });

  /** The desktop chunks pane's empty-state text — distinguishes "nothing on this
   * machine" from "the filter hid everything", naming the hidden count so the
   * operator knows to check "show all". */
  protected readonly chunksEmptyText = computed<string>(() => {
    const total = this.machineChunks().length;
    if (total === 0) return 'NO CHUNKS ON THIS MACHINE';
    const hidden = total - this.visibleChunks().length;
    return `${hidden} CHUNK${hidden === 1 ? '' : 'S'} HIDDEN BY THE FILTER — CHECK SHOW ALL`;
  });

  /**
   * One row per chunk on this machine: the chunk's newest lease (the server
   * orders actives first, then the recent-closed block, so the first lease
   * seen per `chunk_id` is the freshest attempt) plus every attempt of the
   * chunk and the derived status — folded once here, handed to the row and the
   * detail dock alike. Each row's `leases` is ordered oldest → newest, so
   * `lease` (the summary/status subject) is that list's own newest entry.
   */
  protected readonly machineChunks = computed<MachineChunkRow[]>(() => {
    const dashboard = this.dashboardQuery.data();
    const facts = {
      escalatedChunkIds: new Set((dashboard?.escalations?.items ?? []).map((esc) => esc.chunk_id)),
      takeoverChunkIds: new Set((dashboard?.takeovers?.items ?? []).map((tko) => tko.chunk_id)),
      askChunkIds: new Set((dashboard?.asks?.items ?? []).map((ask) => ask.chunk_id)),
    };
    // Group by chunk in server order (newest attempt first); the Map preserves
    // first-seen insertion order, so the rows keep the newest-lease-first order.
    const grouped = new Map<string, runnerApi.LeaseView[]>();
    for (const lease of this.leases()) {
      const group = grouped.get(lease.chunk_id);
      if (group) group.push(lease);
      else grouped.set(lease.chunk_id, [lease]);
    }
    const rows: MachineChunkRow[] = [];
    for (const group of grouped.values()) {
      const newest = group[0];
      rows.push({
        lease: newest,
        leases: [...group].reverse(), // oldest → newest
        status: deriveMachineChunkStatus(newest, facts),
      });
    }
    return rows;
  });

  /** The chunks list's "show all" filter state (issue #134) — plain UI state,
   * unchecked by default. Client-side only: narrows what {@link visibleChunks}
   * renders, never the server-side `RECENT_LEASE_LIMIT`-bounded `/api/leases` read. */
  protected readonly showAllChunks = signal(false);

  /** The chunks list's visible rows — {@link machineChunks} itself when
   * {@link showAllChunks} is checked, else rows whose derived
   * {@link MachineChunkStatus.tone} isn't `done`/`idle`. Filters on the
   * *derived* status (not raw lease state), so a closed lease with an open
   * escalation still shows as `NEEDS HUMAN`. Selection stays keyed off the
   * unfiltered {@link machineChunks}, so a hidden chunk is still deep-linkable.
   * Desktop-only (issue #134) — {@link LocalPanelMobile} takes the unfiltered
   * list directly; mobile's own filter is out of scope here. */
  protected readonly visibleChunks = computed<MachineChunkRow[]>(() => {
    if (this.showAllChunks()) return this.machineChunks();
    return this.machineChunks().filter((chunk) => chunk.status.tone !== 'done' && chunk.status.tone !== 'idle');
  });

  /** The open-ask count for the asks panel's header note — also read by the
   * app root's own mobile tab bar (issue #313) off the same shared dashboard
   * query, folded independently there rather than through this component. */
  protected readonly openAskCount = computed(() => (this.dashboardQuery.data()?.asks?.items ?? []).length);

  /** What is open in the panel, held in the URL's `?chunk=` via the shared
   * {@link injectChunkUrlSelection} — the router coupling lives there, not
   * here. Carries no `attempt` selection: per-attempt selection lives on the
   * chunk detail route, whose own `chunk-detail-page.ts` is the single owner
   * of `?attempt=` — the only site that reads it and the only one that writes
   * it. This panel neither reads nor clears it; the one link into that route
   * carries no query params at all (`machine-detail-header.ts`), so a stale
   * `attempt` cannot reach it either. */
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
  protected readonly selectedChunkLeases = computed<readonly runnerApi.LeaseView[]>(() => {
    const chunkId = this.selectedChunkId();
    if (chunkId === null) return [];
    return this.machineChunks().find((chunk) => chunk.lease.chunk_id === chunkId)?.leases ?? [];
  });

  protected readonly selectedStatus = computed<MachineChunkStatus | null>(() => {
    const chunkId = this.selectedChunkId();
    if (chunkId === null) return null;
    return this.machineChunks().find((chunk) => chunk.lease.chunk_id === chunkId)?.status ?? null;
  });

  /** The open escalation for the selected chunk, when one exists — carries the resume command. */
  protected readonly selectedEscalation = computed<runnerApi.EscalationView | null>(() => {
    const chunkId = this.selectedChunkId();
    if (chunkId === null) return null;
    return (this.dashboardQuery.data()?.escalations?.items ?? []).find((esc) => esc.chunk_id === chunkId) ?? null;
  });
}
