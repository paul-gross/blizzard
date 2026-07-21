import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { type KitAsyncStateValue, MobileTabBar, type MobileTabItem, type runnerApi, ViewportService } from 'fleet';

import { type MachineChunkStatus, deriveMachineChunkStatus } from './chunk-status';
import { injectRunnerLeasesQuery } from './leases.query';
import { LocalPanelLayout } from './local-panel-layout';
import { LocalPanelMobile } from './local-panel-mobile';
import {
  injectRunnerAsksQuery,
  injectRunnerEscalationsQuery,
  injectRunnerTakeoversQuery,
} from './status.query';

/** One row in the machine-chunks list: a chunk's newest lease plus its derived
 * machine-side status, pre-folded so the layout needs no second read. `leases`
 * carries *every* attempt of the chunk (oldest → newest) for the detail dock's
 * per-attempt tabs; `lease` is `leases`' newest entry — the row and the summary
 * both render off it. */
export interface MachineChunkRow {
  readonly lease: runnerApi.LeaseView;
  readonly leases: readonly runnerApi.LeaseView[];
  readonly status: MachineChunkStatus;
}

/**
 * The runner's machine-local panel — the data-orchestration container
 * (issue #80). Owns the four local-API query injections, the one derived-status
 * fold ({@link deriveMachineChunkStatus}), and the selection — which chunk is
 * open and which attempt tab is active, both bound to the URL's query params so
 * a link is shareable and a reload keeps its place (issue #99). Every panel below
 * it (via {@link LocalPanelLayout}) is presentational or owns just its own read.
 *
 * The fold and the selection stay here rather than in the layout, per the
 * epic's design decision: the layout takes `machineChunks`/`selected*` as
 * plain inputs, so it is testable without a runner-client stub. The URL is the
 * single source of truth — the panel derives its selection from the query params
 * and every click writes them back, never the reverse.
 */
@Component({
  selector: 'local-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [LocalPanelLayout, LocalPanelMobile, MobileTabBar],
  template: `
    <div class="lp-shell">
      <div class="lp-content">
        @if (mode() === 'desktop') {
          <local-panel-layout
            [connection]="connection()"
            [activeLeases]="activeLeases()"
            [leasesTriadState]="leasesTriadState()"
            [chunksTriadState]="chunksTriadState()"
            [machineChunks]="machineChunks()"
            [openAskCount]="openAskCount()"
            [selectedChunkId]="selectedChunkId()"
            [selectedChunkLeases]="selectedChunkLeases()"
            [selectedAttemptLeaseId]="selectedAttemptLeaseId()"
            [selectedStatus]="selectedStatus()"
            [selectedEscalation]="selectedEscalation()"
            (selectLease)="selectLease($event)"
            (selectChunk)="selectChunk($event)"
            (selectAttempt)="selectAttempt($event)"
          />
        } @else {
          @defer (on immediate) {
            <local-panel-mobile
              [activeLeases]="activeLeases()"
              [leasesTriadState]="leasesTriadState()"
              [chunksTriadState]="chunksTriadState()"
              [machineChunks]="machineChunks()"
              [openAskCount]="openAskCount()"
            />
          }
        }
      </div>
      @if (mode() === 'mobile') {
        <fleet-mobile-tab-bar [items]="tabItems()" testid="local-panel-mobile-tab-bar" />
      }
    </div>
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
    }
    .lp-shell {
      display: flex;
      flex-direction: column;
      height: 100%;
    }
    .lp-content {
      flex: 1;
      min-height: 0;
      overflow-y: auto;
    }
  `,
})
export class LocalPanel {
  /** A short connection/health status shown in the header (e.g. `ok`, `offline`). */
  readonly connection = input('—');

  /** The page-level shell picker (`../docs/designs/mobile/README.md`'s
   * "adaptive shells over shared guts") — desktop renders the existing
   * three-column {@link LocalPanelLayout} unchanged; mobile renders
   * {@link LocalPanelMobile} instead, `@defer`-loaded so the desktop bundle
   * doesn't carry it, plus the persistent {@link MobileTabBar} below the
   * scrolling `.lp-content` (mirroring the hub app-root's own placement,
   * issue #92) so it never scrolls out of view. The viewport override itself
   * lives behind each shell's own header menu (`KitMenu`, mobile polish
   * feedback item 5) — `LocalPanelLayout`'s `.lp-header` and
   * `LocalPanelMobile`'s shared `MobileTitlebar` menu slot — rather than an
   * always-visible strip above both. */
  protected readonly viewport = inject(ViewportService);

  protected readonly mode = this.viewport.mode;

  protected readonly leasesQuery = injectRunnerLeasesQuery();
  protected readonly asksQuery = injectRunnerAsksQuery();
  protected readonly escalationsQuery = injectRunnerEscalationsQuery();
  protected readonly takeoversQuery = injectRunnerTakeoversQuery();

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

  /** The machine-chunks list's async triad state — shares the leases query
   * (the same read the rows fold from), so it mirrors its loading/error state. */
  protected readonly chunksTriadState = computed<KitAsyncStateValue>(() => {
    if (this.leasesQuery.isPending()) return 'loading';
    if (this.leasesQuery.isError()) return 'error';
    return this.machineChunks().length === 0 ? 'empty' : 'ready';
  });

  /**
   * One row per chunk on this machine: the chunk's newest lease (the server
   * orders actives first, then the recent-closed block, so the first lease
   * seen per `chunk_id` is the freshest attempt) plus every attempt of the
   * chunk and the derived status — folded once here, handed to the row and the
   * detail dock alike. Each row's `leases` is ordered oldest → newest for the
   * detail dock's attempt tabs; `lease` (the summary/status subject) is that
   * list's newest entry.
   */
  protected readonly machineChunks = computed<MachineChunkRow[]>(() => {
    const facts = {
      escalatedChunkIds: new Set((this.escalationsQuery.data() ?? []).map((esc) => esc.chunk_id)),
      takeoverChunkIds: new Set((this.takeoversQuery.data() ?? []).map((tko) => tko.chunk_id)),
      askChunkIds: new Set((this.asksQuery.data() ?? []).map((ask) => ask.chunk_id)),
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
        leases: [...group].reverse(), // oldest → newest for the attempt tabs
        status: deriveMachineChunkStatus(newest, facts),
      });
    }
    return rows;
  });

  /** The open-ask count for the asks panel's header note. */
  protected readonly openAskCount = computed(() => (this.asksQuery.data() ?? []).length);

  /**
   * The mobile bottom tab bar's items (issue #92) — Machine is this shell's
   * one always-current screen (the runner app has no *page* routes — the router
   * carries only the panel's selection query params, issue #99 — so Machine is a
   * statically `active` tab rather than a routed one, unlike the hub's Board);
   * Asks carries the same {@link openAskCount} the local-asks
   * section's own header note reads; Transcripts has no mobile screen of its
   * own yet (a future chunk), so it renders inert — the same "not yet"
   * treatment the hub gives Asks/Fleet today.
   */
  protected readonly tabItems = computed<readonly MobileTabItem[]>(() => [
    { testid: 'tab-machine', label: 'Machine', active: true },
    {
      testid: 'tab-asks-runner',
      label: 'Asks',
      inert: true,
      badge: this.openAskCount(),
      badgeTestid: 'tab-asks-runner-badge',
    },
    { testid: 'tab-transcripts', label: 'Transcripts', inert: true },
  ]);

  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  /**
   * The URL's selection query params — the source of truth for what is open in
   * the panel (issue #99). `chunk` names the selected chunk, `attempt` the
   * selected attempt lease within it; the panel state derives from these, and
   * every selection writes them (never the other way around), so the URL is
   * copyable, refresh-safe, and back/forward-navigable. Read as a signal off the
   * router's `queryParamMap`, seeded from the current snapshot so the first
   * render already reflects a deep-linked URL.
   */
  private readonly queryParams = toSignal(this.route.queryParamMap, {
    initialValue: this.route.snapshot.queryParamMap,
  });

  /**
   * The `chunk_id` currently selected, off the URL's `chunk` param (or `null`).
   * A lease row selects its chunk too ({@link selectLease}) — the lease rail and
   * the chunks list share one selection, reflected on both. An id naming a chunk
   * not on this machine degrades to no-selection without error: nothing in the
   * list matches it, and {@link selectedChunkLeases} falls through to empty.
   */
  protected readonly selectedChunkId = computed<string | null>(() => this.queryParams().get('chunk'));

  /** Write a chunk selection to the URL, clearing any stale `attempt` (attempt
   * lease ids are chunk-specific, so a new chunk defaults to its newest). */
  protected selectChunk(chunkId: string): void {
    this.writeSelection(chunkId, null);
  }

  /** Selecting a lease row selects its chunk — the shared selection both rails
   * reflect; the detail dock defaults to the chunk's newest attempt. */
  protected selectLease(leaseId: string): void {
    const lease = this.leases().find((candidate) => candidate.lease_id === leaseId);
    if (lease) this.writeSelection(lease.chunk_id, null);
  }

  /** Write an attempt pick to the URL, keeping the current chunk selection. */
  protected selectAttempt(leaseId: string): void {
    this.writeSelection(this.selectedChunkId(), leaseId);
  }

  /** Merge the selection into the URL's query params — a client-side navigation
   * (no reload) that pushes a history entry, so back/forward walk the selection
   * history. `null` clears a param. */
  private writeSelection(chunkId: string | null, attemptLeaseId: string | null): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { chunk: chunkId, attempt: attemptLeaseId },
      queryParamsHandling: 'merge',
    });
  }

  /**
   * The selected chunk's attempts (oldest → newest) — what the detail dock
   * renders: its summary/status off the newest, one transcript tab per attempt.
   * Empty when nothing is selected.
   */
  protected readonly selectedChunkLeases = computed<readonly runnerApi.LeaseView[]>(() => {
    const chunkId = this.selectedChunkId();
    if (chunkId === null) return [];
    return this.machineChunks().find((chunk) => chunk.lease.chunk_id === chunkId)?.leases ?? [];
  });

  /**
   * The attempt whose transcript the dock shows — the URL's `attempt` lease id
   * when it still names an attempt of the selected chunk, else the newest attempt
   * (the default). Deriving the *effective* pick here (rather than trusting the
   * raw param) folds in every fallback the old in-dock state carried: a poll
   * refresh keeps the same attempt (its id is unchanged), while a pick that ages
   * out of the recent-lease window — or one left over from another chunk — is no
   * longer among the leases, so it falls back to newest.
   */
  protected readonly selectedAttemptLeaseId = computed<string | null>(() => {
    const leases = this.selectedChunkLeases();
    const newest = leases.at(-1) ?? null;
    const wanted = this.queryParams().get('attempt');
    if (wanted !== null && leases.some((att) => att.lease_id === wanted)) return wanted;
    return newest?.lease_id ?? null;
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
    return (this.escalationsQuery.data() ?? []).find((esc) => esc.chunk_id === chunkId) ?? null;
  });
}
