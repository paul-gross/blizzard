import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import type { runnerApi } from 'fleet';

import { BrandMark, KitAsyncState, type KitAsyncStateValue, KitPanel } from 'fleet';

import { AgentRow } from './agent-row';
import { ChunkDetail } from './chunk-detail';
import { ChunkRow } from './chunk-row';
import { type MachineChunkStatus, deriveMachineChunkStatus } from './chunk-status';
import { EnvList } from './env-list';
import { FactLog } from './fact-log';
import { injectRunnerLeasesQuery } from './leases.query';
import { LocalAsks } from './local-asks';
import { LocalInfo } from './local-info';
import {
  injectRunnerAsksQuery,
  injectRunnerEscalationsQuery,
  injectRunnerTakeoversQuery,
} from './status.query';

/**
 * The runner's machine-local panel — the runner app's own view, shaped like
 * the discovery mock's machine panel: a three-column grid over the runner's
 * hub-free local API (5s polls, no SSE — the runner has no event stream).
 *
 * - **Left (340px)** — liveness: the *active* leases (closed rows are history,
 *   not liveness — they live on the chunks list), each with a heartbeat
 *   freshness bar, over the held-environments rail, split 60/40.
 * - **Center (1fr)** — work: the chunks on this machine (one row per chunk,
 *   PM-enriched, derived status in the hub board's colors) over the machine
 *   detail dock for the selected chunk, transcript inline.
 * - **Right (330px)** — the machine's account of itself: the hub link
 *   (endpoint, reachability, last flush, buffer), the open local asks, and
 *   the local fact log off the outbound ledger.
 *
 * This shell owns the one derived-status fold ({@link deriveMachineChunkStatus})
 * and the selection state; every panel below it is presentational or owns just
 * its own read. Color resolves through the shared design tokens (`fleet`
 * library, design/tokens.css), never hard-coded hex.
 */
@Component({
  selector: 'fleet-local-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [AgentRow, BrandMark, ChunkDetail, ChunkRow, EnvList, FactLog, KitAsyncState, KitPanel, LocalAsks, LocalInfo],
  template: `
    <div class="lp" data-testid="local-panel">
      <header class="lp-header">
        <div class="brand">
          <fleet-brand-mark [size]="24" />
          <div class="brand-text">blizzard<small>runner · machine panel</small></div>
        </div>
        <div class="spacer"></div>
        <div class="conn" data-testid="conn">
          <span class="conn-lbl">Runner</span>
          <span class="v">{{ connection() }}</span>
        </div>
      </header>
      <main class="cols">
        <section class="col left">
          <fleet-kit-panel
            class="leases-panel"
            data-testid="lease-pane"
            label="leases · heartbeat freshness"
          >
            <span header class="p-note" data-testid="lease-count">{{ activeLeases().length }} live</span>
            <fleet-kit-async-state
              [state]="leasesTriadState()"
              loadingText="LOADING…"
              loadingTestid="loading-state"
              errorText="LEASES UNAVAILABLE — RUNNER LOCAL API UNREACHABLE"
              errorTestid="error-state"
              emptyText="NO LIVE LEASES — LOOP IDLE OR PAUSED"
              emptyTestid="empty-state"
            >
              <div class="rows" data-testid="lease-rows">
                @for (lease of activeLeases(); track lease.lease_id) {
                  <fleet-agent-row
                    [agent]="lease"
                    [selected]="lease.chunk_id === selectedChunkId()"
                    (selectLease)="selectLease($event)"
                  />
                }
              </div>
            </fleet-kit-async-state>
          </fleet-kit-panel>
          <fleet-kit-panel class="envs-panel" label="environments · bindings ride the lease">
            <fleet-env-list />
          </fleet-kit-panel>
        </section>
        <section class="col center">
          <fleet-kit-panel class="chunks-panel" data-testid="chunks-pane" label="chunks on this machine · derived status">
            <fleet-kit-async-state
              [state]="chunksTriadState()"
              loadingText="LOADING…"
              errorText="CHUNKS UNAVAILABLE — RUNNER LOCAL API UNREACHABLE"
              emptyText="NO CHUNKS ON THIS MACHINE"
              emptyTestid="chunks-empty"
            >
              @for (chunk of machineChunks(); track chunk.lease.chunk_id) {
                <fleet-chunk-row
                  [lease]="chunk.lease"
                  [status]="chunk.status"
                  [selected]="chunk.lease.chunk_id === selectedChunkId()"
                  (selectChunk)="selectedChunkId.set($event)"
                />
              }
            </fleet-kit-async-state>
          </fleet-kit-panel>
          <div class="detail-frame">
            <fleet-chunk-detail
              [lease]="selectedLease()"
              [status]="selectedStatus()"
              [escalation]="selectedEscalation()"
            />
          </div>
        </section>
        <section class="col right">
          <fleet-kit-panel class="hub-panel" label="hub · outbound only, nothing dials in">
            <fleet-local-info />
          </fleet-kit-panel>
          <fleet-kit-panel class="asks-panel" label="local asks · answers live at the hub">
            <span header class="p-note">{{ openAskCount() }} open</span>
            <fleet-local-asks />
          </fleet-kit-panel>
          <fleet-kit-panel class="facts-panel" label="local fact log · runner store">
            <fleet-fact-log />
          </fleet-kit-panel>
        </section>
      </main>
    </div>
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
      background: var(--bg);
      color: var(--text);
      font-family: var(--mono);
      font-size: var(--fs-base);
      font-variant-numeric: tabular-nums;
    }
    .lp {
      display: flex;
      flex-direction: column;
      height: 100%;
    }
    .conn-lbl {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
    }
    .lp-header {
      flex: none;
      display: flex;
      align-items: stretch;
      height: 40px;
      border-bottom: 1px solid var(--bezel);
      background: linear-gradient(180deg, var(--header-hi), var(--header-lo));
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 9px;
      padding: 0 14px;
      border-right: 1px solid var(--line);
      white-space: nowrap;
    }
    .brand-text {
      display: flex;
      flex-direction: column;
      justify-content: center;
      color: var(--amber-hi);
      font-size: var(--fs-lg);
      letter-spacing: 0.28em;
      text-transform: uppercase;
    }
    .brand small {
      color: var(--label);
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
    }
    .spacer {
      flex: 1;
      border-right: 1px solid var(--line);
    }
    .conn {
      display: flex;
      flex-direction: column;
      justify-content: center;
      padding: 0 14px;
    }
    .conn .v {
      color: var(--cyan);
      font-size: var(--fs-lg);
    }
    .cols {
      flex: 1;
      min-height: 0;
      display: grid;
      grid-template-columns: 340px 1fr 330px;
      gap: 6px;
      padding: 6px;
    }
    .col {
      display: flex;
      flex-direction: column;
      gap: 6px;
      min-height: 0;
      min-width: 0;
    }
    /* This shell's panel chrome is a flat background + gradient header, unlike
       fleet's own gradient-panel/overlay-header scheme — these two kit-panel
       custom-property hooks retarget the kit panel's chrome to match, without
       forking the component. Applies to every kit panel this shell renders. */
    fleet-kit-panel {
      --kit-panel-bg: var(--panel);
      --kit-panel-header-bg: linear-gradient(180deg, var(--header-hi), var(--header-lo));
    }
    .p-note {
      color: var(--label-dim);
      font-size: var(--fs-label);
      letter-spacing: 0.1em;
    }
    .detail-frame {
      display: flex;
      flex-direction: column;
      min-height: 0;
      background: var(--panel);
      border: 1px solid var(--bezel);
      flex: 1.15;
    }
    /* The mock's split weights: leases over envs 60/40; chunks under detail 1:1.15. */
    fleet-kit-panel.leases-panel {
      flex: 1.5;
    }
    fleet-kit-panel.envs-panel {
      flex: 1;
    }
    fleet-kit-panel.chunks-panel {
      flex: 1;
    }
    fleet-kit-panel.hub-panel {
      flex: none;
    }
    fleet-kit-panel.asks-panel {
      flex: 1;
    }
    fleet-kit-panel.facts-panel {
      flex: 1.25;
    }
    .rows {
      display: flex;
      flex-direction: column;
    }
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
  protected readonly leases = computed(() => this.leasesQuery.data() ?? []);

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
  protected readonly machineChunks = computed<{ lease: runnerApi.LeaseView; status: MachineChunkStatus }[]>(() => {
    const facts = {
      escalatedChunkIds: new Set((this.escalationsQuery.data() ?? []).map((esc) => esc.chunk_id)),
      takeoverChunkIds: new Set((this.takeoversQuery.data() ?? []).map((tko) => tko.chunk_id)),
      askChunkIds: new Set((this.asksQuery.data() ?? []).map((ask) => ask.chunk_id)),
    };
    const seen = new Set<string>();
    const rows: { lease: runnerApi.LeaseView; status: MachineChunkStatus }[] = [];
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
