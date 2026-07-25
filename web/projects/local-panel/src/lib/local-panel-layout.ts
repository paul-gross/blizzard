import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import type { runnerApi, StatCell } from 'fleet';

import { BoardHeader, KitAsyncState, type KitAsyncStateValue, KitAvatar, KitMenu, KitPanel, ViewportToggle } from 'fleet';

import { AgentRow } from './agent-row';
import { MachineDetail } from './chunk-detail';
import type { MachineChunkStatus } from './chunk-status';
import { ChunkRow } from './chunk-row';
import { EnvList } from './env-list';
import { FactLog } from './fact-log';
import { LocalAsks } from './local-asks';
import { LocalIdentity } from './local-identity';
import { LocalInfo } from './local-info';
import { LocalPauseControl } from './local-pause-control';
import type { MachineChunkRow } from './local-panel';

/**
 * The runner's machine-local panel's layout half (issue #80) — shaped like
 * the discovery mock's machine panel: a three-column grid over the runner's
 * hub-free local API.
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
 * Presentational only: it renders exactly the leases/chunks/selection it is
 * handed and emits `selectLease`/`selectChunk`; the derived-status fold and
 * the selection state live in the container ({@link LocalPanel}). Color
 * resolves through the shared design tokens (`fleet` library,
 * design/tokens.css), never hard-coded hex.
 *
 * The header's own {@link KitMenu} buries {@link ViewportToggle} (mobile
 * polish feedback item 5) — this is the existing header region the desktop
 * shell's override lives behind now, replacing {@link LocalPanel}'s old
 * always-visible `.viewport-strip`. Its trigger is the shared {@link KitAvatar}
 * profile glyph (issue #132) rather than the menu's default `⋮`, the same
 * trigger the hub's `AppNavMenu` projects — one shared component so both
 * shells' profile menus render identically.
 *
 * The titlebar itself is the shared {@link BoardHeader} (issue #131) — the same
 * 48px chrome the hub board renders, rather than a bespoke local one. It carries
 * this machine's own capacity cells ({@link headerStats}, folded by the container
 * from the runner local API's status + environments reads) and a real connection
 * state, never a placeholder. {@link LocalPauseControl} (issue #133), `local-identity`,
 * and the shell's {@link KitMenu} ride along in the header's `[header-trailing]`
 * slot — each a self-fetching mini-container of its own, composed here without
 * this layout or {@link BoardHeader} knowing anything about pause state or identity.
 */
@Component({
  selector: 'local-panel-layout',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    AgentRow,
    BoardHeader,
    MachineDetail,
    ChunkRow,
    EnvList,
    FactLog,
    KitAsyncState,
    KitAvatar,
    KitMenu,
    KitPanel,
    LocalAsks,
    LocalIdentity,
    LocalInfo,
    LocalPauseControl,
    ViewportToggle,
  ],
  template: `
    <div class="lp" data-testid="local-panel">
      <fleet-board-header [connection]="connection()" connectionLabel="Runner" tagline="runner · machine panel" [stats]="headerStats()">
        <local-pause-control header-trailing />
        <local-identity header-trailing />
        <fleet-kit-menu header-trailing class="menu" ariaLabel="Profile menu" testid="local-panel-menu">
          <fleet-kit-avatar trigger />
          <fleet-viewport-toggle />
        </fleet-kit-menu>
      </fleet-board-header>
      <main class="cols">
        <section class="col left">
          <fleet-kit-panel
            class="leases-panel"
            data-testid="lease-pane"
            label="active leases"
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
                  <local-agent-row
                    [agent]="lease"
                    [selected]="lease.chunk_id === selectedChunkId()"
                    (selectLease)="selectLease.emit($event)"
                  />
                }
              </div>
            </fleet-kit-async-state>
          </fleet-kit-panel>
          <fleet-kit-panel class="envs-panel" label="environments">
            <local-env-list />
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
                <local-chunk-row
                  [lease]="chunk.lease"
                  [status]="chunk.status"
                  [selected]="chunk.lease.chunk_id === selectedChunkId()"
                  (selectChunk)="selectChunk.emit($event)"
                />
              }
            </fleet-kit-async-state>
          </fleet-kit-panel>
          <div class="detail-frame">
            <local-machine-detail
              [leases]="selectedChunkLeases()"
              [activeAttemptLeaseId]="selectedAttemptLeaseId()"
              [status]="selectedStatus()"
              [escalation]="selectedEscalation()"
              (selectAttempt)="selectAttempt.emit($event)"
            />
          </div>
        </section>
        <section class="col right">
          <fleet-kit-panel class="hub-panel" label="hub">
            <local-info />
          </fleet-kit-panel>
          <fleet-kit-panel class="asks-panel" label="local asks">
            <span header class="p-note">{{ openAskCount() }} open</span>
            <local-asks />
          </fleet-kit-panel>
          <fleet-kit-panel class="facts-panel" label="local fact log">
            <local-fact-log />
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
    .menu {
      display: flex;
      align-items: center;
      padding: 0 10px;
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
    /* This layout's panel chrome is a flat background + gradient header, unlike
       fleet's own gradient-panel/overlay-header scheme — these two kit-panel
       custom-property hooks retarget the kit panel's chrome to match, without
       forking the component. Applies to every kit panel this layout renders. */
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
    fleet-kit-panel.envs-panel,
    fleet-kit-panel.chunks-panel,
    fleet-kit-panel.asks-panel {
      flex: 1;
    }
    fleet-kit-panel.hub-panel {
      flex: none;
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
export class LocalPanelLayout {
  /** A short connection/health status shown in the header (e.g. `ok`, `offline`). */
  readonly connection = input('—');

  /** The header's live stat cells — environments in use/capacity and active
   * agent leases/capacity (issue #131), pre-folded by the container from the
   * runner local API's status + environments reads. */
  readonly headerStats = input.required<readonly StatCell[]>();

  /** The active leases for the liveness rail. */
  readonly activeLeases = input.required<readonly runnerApi.LeaseView[]>();

  /** The leases rail's async triad state. */
  readonly leasesTriadState = input.required<KitAsyncStateValue>();

  /** The machine-chunks list's async triad state. */
  readonly chunksTriadState = input.required<KitAsyncStateValue>();

  /** One row per chunk on this machine, pre-folded by the container. */
  readonly machineChunks = input.required<readonly MachineChunkRow[]>();

  /** The open-ask count for the asks panel's header note. */
  readonly openAskCount = input.required<number>();

  /** The `chunk_id` currently selected, or `null`. */
  readonly selectedChunkId = input.required<string | null>();

  /** The selected chunk's attempts (oldest → newest) — what the detail dock
   * renders: summary/status off the newest, one transcript tab per attempt. */
  readonly selectedChunkLeases = input.required<readonly runnerApi.LeaseView[]>();

  /** The attempt whose transcript the detail dock shows — the container's
   * URL-derived effective pick (issue #99), fed straight to the detail dock. */
  readonly selectedAttemptLeaseId = input.required<string | null>();

  readonly selectedStatus = input.required<MachineChunkStatus | null>();

  /** The open escalation for the selected chunk, when one exists. */
  readonly selectedEscalation = input.required<runnerApi.EscalationView | null>();

  /** Emitted with a lease id when the operator selects a lease row — the
   * lease rail and the chunks list share one selection, so this drives both. */
  readonly selectLease = output<string>();

  /** Emitted with a chunk id when the operator selects a chunk row. */
  readonly selectChunk = output<string>();

  /** Emitted with an attempt lease id when the operator picks an attempt tab in
   * the detail dock — the container writes it to the URL. */
  readonly selectAttempt = output<string>();
}
