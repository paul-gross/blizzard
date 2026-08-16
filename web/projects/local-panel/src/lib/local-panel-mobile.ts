import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { CdkMenuTrigger } from '@angular/cdk/menu';
import {
  KitAsyncState,
  type KitAsyncStateValue,
  KitBackBar,
  KitMenuItem,
  KitMenuPanel,
  KitPanel,
  KitPanelHeader,
  MobileTitlebar,
  ViewportMenu,
  type runnerApi,
} from 'fleet';

import { AgentRow } from './agent-row';
import { ChunkCard } from './chunk-card';
import { MachineDetail } from './chunk-detail';
import type { MachineChunkStatus } from './chunk-status';
import { LocalAsks } from './local-asks';
import { LocalIdentity } from './local-identity';
import { LocalInfo } from './local-info';
import type { MachineChunkRow } from './local-panel';
import { injectRunnerSessionQuery, signedInUsername } from './auth.query';
import { injectRunnerDashboardQuery } from './status.query';

/**
 * The runner local panel's mobile shell (mobile mockups, `../docs/designs/mobile/README.md`)
 * — a single scrolling column stacking the glance-relevant pieces in attention
 * order: machine info/status first (the hub link, `local-info`), then
 * agents/leases (`AgentRow` already carries its own heartbeat-freshness bar
 * per row), then chunks on this machine, then local asks. Every section is a
 * desktop-layout component reused verbatim (`bzh:frontend-kit`) — this shell
 * only orders and stacks them, it never forks or re-styles their internals.
 *
 * Selecting a chunk (a {@link ChunkCard} tap, or an {@link AgentRow} tap —
 * which selects that lease's chunk, the same shared selection the desktop
 * rails have) drills down to a **detail screen** in place of the list: the
 * mobile answer to the desktop layout's side-by-side detail dock, which
 * assumes width a single column doesn't have. The screen itself is
 * {@link MachineDetail} reused verbatim (`bzh:frontend-kit`) — the same
 * execution facts (lease/session/pid/env/workdir/heartbeat) the desktop dock
 * renders; this shell only swaps which of the two screens is mounted and adds
 * the back affordance a drill-down needs. Selection itself stays the container's
 * (and the URL's, issue #99), so a detail screen is deep-linkable and the
 * device back button walks out of it like any other navigation.
 *
 * {@link LocalPauseControl} (issue #133) is likewise **not** mounted here —
 * a deliberate scope decision, not an oversight: #133 shipped desktop-only,
 * so a mobile operator sees neither the local pause toggle nor the "paused
 * by hub" badge today. Mounting it (and wiring a home for it in this single
 * scrolling column) is left to the next mobile chunk.
 *
 * Mounts the shared {@link MobileTitlebar} (issue #92) in place of its old
 * bespoke header — the same fleet component the hub's app-root mounts —
 * burying the appearance switcher behind the titlebar's own overflow menu
 * (mobile polish feedback item 5; the desktop layout's own header hosts one
 * too, so the override stays reachable in both modes). Since the CDK-menu
 * rebuild (issue #161) that menu's panel is declared here and passed to the
 * titlebar as a template — a `CdkMenu` finds its items by a content query,
 * which cannot cross an `<ng-content>` boundary. Its `live` input is
 * this runner's own hub-reachability read (`GET /api/dashboard`'s
 * `runner.hub.reachable`, the same fact `local-info.ts`'s "link" cell
 * renders) — never a new poll, the same severable {@link injectRunnerDashboardQuery}
 * read.
 */
@Component({
  selector: 'local-panel-mobile',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    AgentRow,
    ChunkCard,
    KitAsyncState,
    KitBackBar,
    KitPanel,
  KitPanelHeader,
    LocalAsks,
    LocalIdentity,
    LocalInfo,
    MachineDetail,
    CdkMenuTrigger,
    KitMenuItem,
    KitMenuPanel,
    MobileTitlebar,
    ViewportMenu,
  ],
  template: `
    <div class="lpm" data-testid="local-panel-mobile">
      <fleet-mobile-titlebar [live]="hubReachable()" testid="local-panel-mobile-titlebar" [menu]="shellMenu" />
      <ng-template #shellMenu>
        <fleet-kit-menu-panel testid="local-panel-mobile-titlebar-menu-panel">
          <!-- The identity block renders label-only in here: a role=menu may own
               only menu items, so its logout is the real menu item below, driven
               through the template reference — LocalIdentity stays the one owner
               of the logout call. The item's *gate* is this shell's own session
               read, not the block's: the block is constructed inside the overlay,
               so its signal is still unresolved when CdkMenu picks the initially
               focused item, and a Log out that appears a tick later is one the
               keyboard has already skipped past. -->
          <local-identity #identity variant="label" />
          @if (signedIn()) {
            <fleet-kit-menu-item testid="local-panel-mobile-logout" (triggered)="identity.logout()">
              Log out
            </fleet-kit-menu-item>
          }
          <fleet-kit-menu-item
            testid="local-panel-mobile-appearance"
            submenu
            [cdkMenuTriggerFor]="appearanceMenu"
          >
            Appearance
          </fleet-kit-menu-item>
        </fleet-kit-menu-panel>
      </ng-template>
      <ng-template #appearanceMenu>
        <fleet-viewport-menu testid="local-panel-mobile-appearance-panel" />
      </ng-template>
      @if (detailOpen()) {
        <div class="lpm-detail" data-testid="panel-chunk-detail">
          <button class="back-row" type="button" aria-label="Back to Machine" data-testid="mobile-detail-back" (click)="closeDetail.emit()">
            <fleet-kit-back-bar label="Machine" />
          </button>
          <local-machine-detail
            class="md"
            [leases]="selectedChunkLeases()"
            [status]="selectedStatus()"
            [escalation]="selectedEscalation()"
            (dismiss)="closeDetail.emit()"
          />
        </div>
      } @else {
        <div class="lpm-sections">
          <fleet-kit-panel class="section" label="machine · status" data-testid="mobile-info-pane">
            <local-info />
          </fleet-kit-panel>
          <fleet-kit-panel class="section" label="agents · leases" data-testid="mobile-agents-pane">
            <span fleetKitPanelHeader class="p-note" data-testid="mobile-lease-count">{{ activeLeases().length }} live</span>
            <div class="pane-body">
              <fleet-kit-async-state
                [state]="leasesTriadState()"
                loadingText="LOADING…"
                loadingTestid="loading-state"
                errorText="LEASES UNAVAILABLE — RUNNER LOCAL API UNREACHABLE"
                errorTestid="error-state"
                emptyText="NO LIVE LEASES — LOOP IDLE OR PAUSED"
                emptyTestid="empty-state"
              >
                <div class="rows">
                  @for (lease of activeLeases(); track lease.lease_id) {
                    <local-agent-row [agent]="lease" (selectLease)="selectLease.emit($event)" />
                  }
                </div>
              </fleet-kit-async-state>
            </div>
          </fleet-kit-panel>
          <fleet-kit-panel class="section" label="chunks on this machine" data-testid="mobile-chunks-pane">
            <div class="pane-body">
              <fleet-kit-async-state
                [state]="chunksTriadState()"
                loadingText="LOADING…"
                errorText="CHUNKS UNAVAILABLE — RUNNER LOCAL API UNREACHABLE"
                emptyText="NO CHUNKS ON THIS MACHINE"
                emptyTestid="chunks-empty"
              >
                @for (chunk of machineChunks(); track chunk.lease.chunk_id) {
                  <local-chunk-card
                    [lease]="chunk.lease"
                    [status]="chunk.status"
                    (selectChunk)="selectChunk.emit($event)"
                  />
                }
              </fleet-kit-async-state>
            </div>
          </fleet-kit-panel>
          <fleet-kit-panel class="section" label="local asks" data-testid="mobile-asks-pane">
            <span fleetKitPanelHeader class="p-note">{{ openAskCount() }} open</span>
            <local-asks />
          </fleet-kit-panel>
        </div>
      }
    </div>
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
      font-family: var(--mono);
      font-size: var(--fs-base);
      font-variant-numeric: tabular-nums;
    }
    /* The titlebar is chrome, not content: it sits outside the scroll region
       so it stays fixed at the top while the sections scroll under it — the
       same shape the hub's app-root gives its own mobile titlebar, and what
       both MobileTitlebar and MobileTabBar already declare flex:none for.
       Scrolling .lpm itself would carry the titlebar away with it. */
    .lpm {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
      overflow: hidden;
    }
    .lpm-sections {
      display: flex;
      flex-direction: column;
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      gap: 8px;
      padding: 8px;
    }
    /* The drill-down screen fills the same box the sections list would have —
       the same sizing MachineDetail gets on desktop. */
    .lpm-detail {
      display: flex;
      flex-direction: column;
      flex: 1;
      min-height: 0;
    }
    .lpm-detail .md {
      flex: 1;
      min-height: 0;
    }
    /* A bare wrapper: KitBackBar owns the row's look and height, this owns only
       the button reset and the focus ring. */
    .back-row {
      flex: none;
      display: block;
      padding: 0;
      border: 0;
      background: none;
      text-align: left;
    }
    .back-row:focus-visible {
      outline: 1px solid var(--cyan);
      outline-offset: -1px;
    }
    /* A positioned box with real height for KitAsyncState's absolutely
       centered status line — these panes size to their content, so an empty
       one would otherwise collapse and paint its message over the pane below. */
    .pane-body {
      position: relative;
      min-height: 56px;
    }
    fleet-kit-panel.section {
      flex: none;
    }
    .p-note {
      color: var(--label-dim);
      font-size: var(--fs-label);
      letter-spacing: 0.1em;
    }
    .rows {
      display: flex;
      flex-direction: column;
    }
  `,
})
export class LocalPanelMobile {
  /** The active leases for the agents/leases section. */
  readonly activeLeases = input.required<readonly runnerApi.LeaseView[]>();

  /** The agents/leases section's async triad state. */
  readonly leasesTriadState = input.required<KitAsyncStateValue>();

  /** The chunks section's async triad state. */
  readonly chunksTriadState = input.required<KitAsyncStateValue>();

  /** One row per chunk on this machine, pre-folded by the container. */
  readonly machineChunks = input.required<readonly MachineChunkRow[]>();

  /** The open-ask count for the local-asks section's header note. */
  readonly openAskCount = input.required<number>();

  /** The selected chunk's attempts (oldest → newest), resolved by the
   * container off the URL's `chunk` param — the detail screen's subject.
   * Empty when nothing is selected, which is also what keeps the shell on
   * the list: a `chunk` naming something not on this machine resolves to no
   * attempts, so the drill-down degrades to the list rather than an empty
   * screen. */
  readonly selectedChunkLeases = input<readonly runnerApi.LeaseView[]>([]);

  /** The selected chunk's derived machine-side status (container-folded). */
  readonly selectedStatus = input<MachineChunkStatus | null>(null);

  /** The selected chunk's open escalation, when there is one. */
  readonly selectedEscalation = input<runnerApi.EscalationView | null>(null);

  /** A chunk card tap — the container writes it to the URL, which opens the
   * detail screen on the next render. */
  readonly selectChunk = output<string>();

  /** An agent row tap — selects that lease's chunk, the same shared selection
   * the desktop rails have. */
  readonly selectLease = output<string>();

  /** The back affordance — the container clears the selection. */
  readonly closeDetail = output<void>();

  /** Whether the detail screen is mounted in place of the sections list. */
  protected readonly detailOpen = computed(() => this.selectedChunkLeases().length > 0);

  /** The titlebar's own severable read (`local-info.ts`'s own instance dedupes
   * on the same query key, so this is not a second poll) — `runner.hub.reachable`
   * off `GET /api/dashboard`, the same fact `local-info`'s "link" cell renders. */
  private readonly dashboardQuery = injectRunnerDashboardQuery();

  /** Whether the hub link is reachable — the titlebar's `live` dot. A
   * malformed body (e.g. a misrouted proxy) must degrade to `false`, not
   * throw mid-render — the same guard `local-info.ts`'s own `view` takes. */
  protected readonly hubReachable = computed(() => this.dashboardQuery.data()?.runner?.hub?.reachable ?? false);

  private readonly sessionQuery = injectRunnerSessionQuery();

  /** Whether a hub username is signed in — the gate on the titlebar menu's
   * `Log out` item. Read here, on a component that has been alive since the
   * shell mounted, rather than off the {@link LocalIdentity} inside the overlay,
   * so the item exists in the panel's very first change detection and
   * `CdkMenu` focuses it as the first item. */
  protected readonly signedIn = computed(() => signedInUsername(this.sessionQuery.data()) !== null);
}
