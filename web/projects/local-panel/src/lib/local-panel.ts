import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { type KitAsyncStateValue, type runnerApi } from 'fleet';

import { type MachineChunkStatus, deriveMachineChunkStatus } from './chunk-status';
import { injectRunnerLeasesQuery } from './leases.query';
import { LocalPanelLayout } from './local-panel-layout';
import {
  injectRunnerAsksQuery,
  injectRunnerEscalationsQuery,
  injectRunnerTakeoversQuery,
} from './status.query';

/** One row in the machine-chunks list: a chunk's newest lease plus its derived
 * machine-side status, pre-folded so the layout needs no second read. */
export interface MachineChunkRow {
  readonly lease: runnerApi.LeaseView;
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
  selector: 'fleet-local-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [LocalPanelLayout],
  template: `
    <fleet-local-panel-layout
      [connection]="connection()"
      [activeLeases]="activeLeases()"
      [leasesTriadState]="leasesTriadState()"
      [chunksTriadState]="chunksTriadState()"
      [machineChunks]="machineChunks()"
      [openAskCount]="openAskCount()"
      [selectedChunkId]="selectedChunkId()"
      [selectedLease]="selectedLease()"
      [selectedStatus]="selectedStatus()"
      [selectedEscalation]="selectedEscalation()"
      (selectLease)="selectLease($event)"
      (selectChunk)="selectedChunkId.set($event)"
    />
  `,
})
export class LocalPanel {
  /** A short connection/health status shown in the header (e.g. `ok`, `offline`). */
  readonly connection = input('—');

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
   * seen per `chunk_id` is the freshest attempt) plus the derived status —
   * folded once here, handed to the row and the detail dock alike.
   */
  protected readonly machineChunks = computed<MachineChunkRow[]>(() => {
    const facts = {
      escalatedChunkIds: new Set((this.escalationsQuery.data() ?? []).map((esc) => esc.chunk_id)),
      takeoverChunkIds: new Set((this.takeoversQuery.data() ?? []).map((tko) => tko.chunk_id)),
      askChunkIds: new Set((this.asksQuery.data() ?? []).map((ask) => ask.chunk_id)),
    };
    const seen = new Set<string>();
    const rows: MachineChunkRow[] = [];
    for (const lease of this.leases()) {
      if (seen.has(lease.chunk_id)) continue;
      seen.add(lease.chunk_id);
      rows.push({ lease, status: deriveMachineChunkStatus(lease, facts) });
    }
    return rows;
  });

  /** The open-ask count for the asks panel's header note. */
  protected readonly openAskCount = computed(() => (this.asksQuery.data() ?? []).length);

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

  /** The selected chunk's newest lease — what the detail dock renders. */
  protected readonly selectedLease = computed<runnerApi.LeaseView | null>(() => {
    const chunkId = this.selectedChunkId();
    if (chunkId === null) return null;
    return this.machineChunks().find((chunk) => chunk.lease.chunk_id === chunkId)?.lease ?? null;
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
