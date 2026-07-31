import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import type { runnerApi, StatCell } from 'fleet';

import { CdkMenuTrigger } from '@angular/cdk/menu';

import {
  BoardHeader,
  KitAsyncState,
  type KitAsyncStateValue,
  KitAvatar,
  KitMenu,
  KitMenuItem,
  KitMenuPanel,
  KitPanel,
  ViewportMenu,
} from 'fleet';

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
 *   work-item-enriched, derived status in the hub board's colors) over the machine
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
 * The header's own {@link KitMenu} buries the appearance switcher (mobile polish
 * feedback item 5), replacing {@link LocalPanel}'s old always-visible
 * `.viewport-strip`. Its trigger is the shared {@link KitAvatar} glyph (issue
 * #132), the same one the hub's `AppNavMenu` projects.
 *
 * The titlebar itself is the shared {@link BoardHeader} (issue #131) — the same
 * 48px chrome the hub board renders, rather than a bespoke local one. It carries
 * this machine's own capacity cells ({@link headerStats}, folded by the container
 * from the runner local API's status + environments reads) and a real connection
 * state, never a placeholder. {@link LocalPauseControl} (issue #133), `local-identity`,
 * and the shell's profile menu ride along in the header's `[header-trailing]`
 * slot — each a self-fetching mini-container of its own, composed here without
 * this layout or {@link BoardHeader} knowing anything about pause state or identity.
 *
 * Because this shell puts three things in that slot where the hub board puts one,
 * it owns how they behave as the header narrows (issue #163): the `@container`
 * rule below drops the pause control and identity at the narrow tier, and the
 * `flex` rules beside it steer the cluster's shrink into the username, so a long
 * one truncates rather than pushing the profile menu off the clipped right edge.
 * A breakpoint alone could not cover that — a name long enough overflows at any
 * width above it. `Log out` is a menu item rather than the identity block's own
 * button so it survives that collapse, and so all three profile menus carry the
 * same two items; the block still owns the session read and the logout call,
 * reached through a template reference for the content-query reason above.
 */
@Component({
  selector: 'local-panel-layout',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    AgentRow,
    BoardHeader,
    CdkMenuTrigger,
    MachineDetail,
    ChunkRow,
    EnvList,
    FactLog,
    KitAsyncState,
    KitAvatar,
    KitMenu,
    KitMenuItem,
    KitMenuPanel,
    KitPanel,
    LocalAsks,
    LocalIdentity,
    LocalInfo,
    LocalPauseControl,
    ViewportMenu,
  ],
  template: `
    <div class="lp" data-testid="local-panel">
      <fleet-board-header [connection]="connection()" connectionLabel="Runner" tagline="runner · machine panel" [stats]="headerStats()">
        <local-pause-control header-trailing />
        <!-- Display-only: this shell's logout is the menu item below, so it does
             not vanish with the header cell at the narrow tier, and all three of
             the app's profile menus carry the same two items. -->
        <local-identity #identity header-trailing variant="label" />
        <fleet-kit-menu
          header-trailing
          class="menu"
          ariaLabel="Profile menu"
          testid="local-panel-menu"
          [menu]="profileMenu"
        >
          <fleet-kit-avatar trigger />
        </fleet-kit-menu>
      </fleet-board-header>
      <ng-template #profileMenu>
        <fleet-kit-menu-panel testid="local-panel-menu-panel">
          @if (identity.username()) {
            <fleet-kit-menu-item testid="local-panel-logout" (triggered)="identity.logout()">Log out</fleet-kit-menu-item>
          }
          <fleet-kit-menu-item testid="local-panel-appearance" submenu [cdkMenuTriggerFor]="appearanceMenu">
            Appearance
          </fleet-kit-menu-item>
        </fleet-kit-menu-panel>
      </ng-template>
      <ng-template #appearanceMenu>
        <fleet-viewport-menu testid="local-panel-appearance-panel" />
      </ng-template>
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
          <div class="detail-frame">
            <local-machine-detail
              [leases]="selectedChunkLeases()"
              [activeAttemptLeaseId]="selectedAttemptLeaseId()"
              [status]="selectedStatus()"
              [escalation]="selectedEscalation()"
              (selectAttempt)="selectAttempt.emit($event)"
              (dismiss)="dismiss.emit()"
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
    /* The menu never gives way; the identity does — this is where BoardHeader's
       shrinkable trailing cluster lands, so the username truncates and the only
       route back to mobile keeps its size at every width. */
    .menu {
      display: flex;
      flex: none;
      align-items: center;
      padding: 0 10px;
    }
    local-pause-control {
      flex: none;
    }
    local-identity {
      min-width: 0;
    }
    /* This layout's own half of the tiered collapse: BoardHeader can only collapse
       the cells it renders, and left standing these two push the profile menu off
       a phone-width header — where, in desktop mode, that menu is this shell's
       ONLY appearance switcher. The container it declares is named, so the rule
       rides the same breakpoint from out here, on nodes this template owns. */
    @container board-header (max-width: 699px) {
      local-pause-control,
      local-identity {
        display: none;
      }
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

  /** Emitted with the checkbox's new checked state when the operator toggles
   * "show all" (issue #134). */
  readonly toggleShowAllChunks = output<boolean>();

  /** Emitted with an attempt lease id when the operator picks an attempt tab in
   * the detail dock — the container writes it to the URL. */
  readonly selectAttempt = output<string>();

  /** Emitted when the operator dismisses the detail dock (issue #185) via its
   * own close button — the container clears the selection. */
  readonly dismiss = output<void>();
}
