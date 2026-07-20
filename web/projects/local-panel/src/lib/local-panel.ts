import { ChangeDetectionStrategy, Component, computed, inject, input, signal } from '@angular/core';
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
 * fold ({@link deriveMachineChunkStatus}), and the chunk selection state; every
 * panel below it (via {@link LocalPanelLayout}) is presentational or owns just
 * its own read.
 *
 * The fold and the selection stay here rather than in the layout, per the
 * epic's design decision: the layout takes `machineChunks`/`selected*` as
 * plain inputs, so it is testable without a runner-client stub.
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
            [selectedStatus]="selectedStatus()"
            [selectedEscalation]="selectedEscalation()"
            (selectLease)="selectLease($event)"
            (selectChunk)="selectedChunkId.set($event)"
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
   * one always-current screen (no router in the runner app, so it is a
   * statically `active` tab rather than a routed one, unlike the hub's
   * Board); Asks carries the same {@link openAskCount} the local-asks
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

  /**
   * The `chunk_id` currently selected on the chunks list, or `null`. A lease
   * row selects its chunk too ({@link selectLease}) — the lease rail and the
   * chunks list share one selection, reflected on both.
   */
  protected readonly selectedChunkId = signal<string | null>(null);

  protected selectLease(leaseId: string): void {
    const lease = this.leases().find((candidate) => candidate.lease_id === leaseId);
    if (lease) this.selectedChunkId.set(lease.chunk_id);
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
