import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import type { runnerApi } from 'fleet';

import { KitAsyncState, type KitAsyncStateValue, KitPanel, KitPanelHeader } from 'fleet';

import { AgentRow } from './agent-row';
import { MachineDetail } from './chunk-detail';
import type { MachineChunkStatus } from './chunk-status';
import { ChunkRow } from './chunk-row';
import { EnvList } from './env-list';
import { LocalAsks } from './local-asks';
import { LocalInfo } from './local-info';
import type { MachineChunkRow } from './local-panel';

/**
 * The runner's machine-local panel's layout half (issue #80) — shaped like
 * the discovery mock's machine panel: a three-column grid over the runner's
 * local API, hub-free save for the rails that proxy through it — the
 * fleet-summary strip (`local-info`).
 *
 * - **Left (340px)** — liveness: the *active* leases (closed rows are history,
 *   not liveness — they live on the chunks list), each with a heartbeat
 *   freshness bar, over the held-environments rail, split 60/40.
 * - **Center (1fr)** — work: the chunks on this machine (one row per chunk,
 *   work-item-enriched, derived status in the hub board's colors) over the machine
 *   detail dock for the selected chunk's execution facts — the transcript and
 *   per-attempt selection moved to the runner-local chunk detail route
 *   (issue #318).
 * - **Right (330px)** — the machine's account of itself: the hub link
 *   (endpoint, reachability, last flush, buffer) and the open local asks. The
 *   local fact log moved to its own `/events` route (issue #313) — full
 *   width there rather than a rail-sized panel.
 *
 * Presentational only: it renders exactly the leases/chunks/selection it is
 * handed and emits `selectLease`/`selectChunk`; the derived-status fold and
 * the selection state live in the container ({@link LocalPanel}). Color
 * resolves through the shared design tokens (`fleet` library,
 * design/tokens.css), never hard-coded hex.
 *
 * Owns no titlebar (issue #325): the shared `BoardHeader` chrome, the pause
 * control/identity/profile-menu trailing cluster, and their narrow-tier
 * collapse all moved up to the app root's own `AppHeader`
 * (`../../runner/src/app/nav/app-header.ts`), the same shelf the hub board's
 * header sits on — mounted once, above the routed tab strip, rather than
 * nested inside this routed layout where it used to render under the tabs on
 * `/board` and not at all on `/events`.
 */
@Component({
  selector: 'local-panel-layout',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [AgentRow, MachineDetail, ChunkRow, EnvList, KitAsyncState, KitPanel, KitPanelHeader, LocalAsks, LocalInfo],
  template: `
    <div class="lp" data-testid="local-panel">
      <main class="cols">
        <section class="col left">
          <fleet-kit-panel
            class="leases-panel"
            data-testid="lease-pane"
            label="active leases"
          >
            <span fleetKitPanelHeader class="p-note" data-testid="lease-count">{{ activeLeases().length }} live</span>
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
            <label class="chunk-filter-bar" data-testid="chunk-filter-bar">
              <input
                type="checkbox"
                data-testid="chunk-filter-show-all"
                [checked]="showAllChunks()"
                (change)="toggleShowAllChunks.emit(!showAllChunks())"
              />
              show all
            </label>
            <fleet-kit-async-state
              [state]="chunksTriadState()"
              loadingText="LOADING…"
              errorText="CHUNKS UNAVAILABLE — RUNNER LOCAL API UNREACHABLE"
              [emptyText]="chunksEmptyText()"
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
          <local-machine-detail
            class="detail-frame"
            [leases]="selectedChunkLeases()"
            [status]="selectedStatus()"
            [escalation]="selectedEscalation()"
            (dismiss)="dismiss.emit()"
          />
        </section>
        <section class="col right">
          <fleet-kit-panel class="hub-panel" label="hub">
            <local-info />
          </fleet-kit-panel>
          <fleet-kit-panel class="asks-panel" label="local asks">
            <span fleetKitPanelHeader class="p-note">{{ openAskCount() }} open</span>
            <local-asks />
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
    .p-note {
      color: var(--label-dim);
      font-size: var(--fs-label);
      letter-spacing: 0.1em;
    }
    /* The chunks list's slim show-all filter bar (issue #134) — a plain
       checkbox row above the list, not a kit component: this is the one
       input the panel needs, not a chip/pill choice set. */
    .chunk-filter-bar {
      flex: none;
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 4px 8px;
      border-bottom: 1px solid var(--line);
      color: var(--label-dim);
      font-size: var(--fs-label);
      letter-spacing: 0.1em;
      text-transform: uppercase;
      cursor: pointer;
    }
    /* Sizing only — local-machine-detail paints its own panel chrome via
       KitPanel now (issue #307), so this no longer hand-copies a
       background/border of its own. */
    .detail-frame {
      min-height: 0;
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
    .rows {
      display: flex;
      flex-direction: column;
    }
  `,
})
export class LocalPanelLayout {
  /** The active leases for the liveness rail. */
  readonly activeLeases = input.required<readonly runnerApi.LeaseView[]>();

  /** The leases rail's async triad state. */
  readonly leasesTriadState = input.required<KitAsyncStateValue>();

  /** The machine-chunks list's async triad state. */
  readonly chunksTriadState = input.required<KitAsyncStateValue>();

  /** The chunks pane's empty-state text — the container distinguishes
   * "nothing on this machine" from "the filter hid everything" (issue #134
   * review fix), so this layout renders whichever text it is handed rather
   * than a literal in the template. */
  readonly chunksEmptyText = input.required<string>();

  /** One row per chunk on this machine, pre-folded by the container **and**
   * already filtered per {@link showAllChunks} (issue #134) — the container
   * owns the fold, this renders exactly the rows it is handed. */
  readonly machineChunks = input.required<readonly MachineChunkRow[]>();

  /** The chunks list's "show all" checkbox state (issue #134) — unchecked
   * (the default) hides a chunk whose newest lease is closed; the container
   * derives {@link machineChunks} from this, so this is display-only here. */
  readonly showAllChunks = input.required<boolean>();

  /** The open-ask count for the asks panel's header note. */
  readonly openAskCount = input.required<number>();

  /** The `chunk_id` currently selected, or `null`. */
  readonly selectedChunkId = input.required<string | null>();

  /** The selected chunk's attempts (oldest → newest) — the detail dock reads
   * only the newest for its summary/status; per-attempt selection and the
   * transcript live on the chunk detail route instead (issue #318). */
  readonly selectedChunkLeases = input.required<readonly runnerApi.LeaseView[]>();

  readonly selectedStatus = input.required<MachineChunkStatus | null>();

  /** The open escalation for the selected chunk, when one exists. */
  readonly selectedEscalation = input.required<runnerApi.EscalationView | null>();

  /** Emitted with a lease id when the operator selects a lease row — the
   * lease rail and the chunks list share one selection, so this drives both. */
  readonly selectLease = output<string>();

  /** Emitted with a chunk id when the operator selects a chunk row. */
  readonly selectChunk = output<string>();

  /** Emitted with the checkbox's new checked state when the operator toggles
   * "show all" (issue #134). */
  readonly toggleShowAllChunks = output<boolean>();

  /** Emitted when the operator dismisses the detail dock (issue #185) via its
   * own close button — the container clears the selection. */
  readonly dismiss = output<void>();
}
