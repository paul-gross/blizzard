import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { KitAsyncState, type KitAsyncStateValue, KitPanel, MobileTitlebar, ViewportToggle, type runnerApi } from 'fleet';

import { AgentRow } from './agent-row';
import { ChunkCard } from './chunk-card';
import { LocalAsks } from './local-asks';
import { LocalInfo } from './local-info';
import type { MachineChunkRow } from './local-panel';
import { injectRunnerStatusQuery } from './status.query';

/**
 * The runner local panel's mobile shell (mobile mockups, `../docs/designs/mobile/README.md`)
 * — a single scrolling column stacking the glance-relevant pieces in attention
 * order: machine info/status first (the hub link, `local-info`), then
 * agents/leases (`AgentRow` already carries its own heartbeat-freshness bar
 * per row), then chunks on this machine, then local asks. Every section is a
 * desktop-layout component reused verbatim (`bzh:frontend-kit`) — this shell
 * only orders and stacks them, it never forks or re-styles their internals.
 *
 * The transcript panel and the wide detail docks (`local-panel-layout`'s
 * center/right columns beyond this) are deliberately absent — they assume
 * width a single column doesn't have, and are the next mobile chunk's work.
 * A tap on an agent/chunk row that would normally open a dock is inert here:
 * `AgentRow`/`ChunkRow` still emit `selectLease`/`selectChunk`, this shell
 * just binds neither.
 *
 * Mounts the shared {@link MobileTitlebar} (issue #92) in place of its old
 * bespoke header — the same fleet component the hub's app-root mounts —
 * burying {@link ViewportToggle} behind the titlebar's own overflow menu
 * (mobile polish feedback item 5; the desktop layout's own header hosts one
 * too, so the override stays reachable in both modes). Its `live` input is
 * this runner's own hub-reachability read (`GET /api/runner`'s
 * `hub.reachable`, the same fact `local-info.ts`'s "link" cell renders) —
 * never a new poll, the same severable {@link injectRunnerStatusQuery} read.
 */
@Component({
  selector: 'local-panel-mobile',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [AgentRow, ChunkCard, KitAsyncState, KitPanel, LocalAsks, LocalInfo, MobileTitlebar, ViewportToggle],
  template: `
    <div class="lpm" data-testid="local-panel-mobile">
      <fleet-mobile-titlebar [live]="hubReachable()" testid="local-panel-mobile-titlebar">
        <fleet-viewport-toggle />
      </fleet-mobile-titlebar>
      <div class="lpm-sections">
        <fleet-kit-panel class="section" label="machine · status" data-testid="mobile-info-pane">
          <local-info />
        </fleet-kit-panel>
        <fleet-kit-panel class="section" label="agents · leases" data-testid="mobile-agents-pane">
          <span header class="p-note" data-testid="mobile-lease-count">{{ activeLeases().length }} live</span>
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
                <local-agent-row [agent]="lease" />
              }
            </div>
          </fleet-kit-async-state>
        </fleet-kit-panel>
        <fleet-kit-panel class="section" label="chunks on this machine" data-testid="mobile-chunks-pane">
          <fleet-kit-async-state
            [state]="chunksTriadState()"
            loadingText="LOADING…"
            errorText="CHUNKS UNAVAILABLE — RUNNER LOCAL API UNREACHABLE"
            emptyText="NO CHUNKS ON THIS MACHINE"
            emptyTestid="chunks-empty"
          >
            @for (chunk of machineChunks(); track chunk.lease.chunk_id) {
              <local-chunk-card [lease]="chunk.lease" [status]="chunk.status" />
            }
          </fleet-kit-async-state>
        </fleet-kit-panel>
        <fleet-kit-panel class="section" label="local asks" data-testid="mobile-asks-pane">
          <span header class="p-note">{{ openAskCount() }} open</span>
          <local-asks />
        </fleet-kit-panel>
      </div>
    </div>
  `,
  styles: `
    :host {
      display: block;
      font-family: var(--mono);
      font-size: var(--fs-base);
      font-variant-numeric: tabular-nums;
    }
    .lpm {
      display: flex;
      flex-direction: column;
    }
    .lpm-sections {
      display: flex;
      flex-direction: column;
      gap: 8px;
      padding: 8px;
    }
    /* This shell's panel chrome matches local-panel-layout's flat background +
       gradient header, unlike fleet's own gradient-panel/overlay-header scheme
       — the same two kit-panel custom-property hooks retarget the chrome
       without forking the component (see local-panel-layout.ts). */
    fleet-kit-panel.section {
      --kit-panel-bg: var(--panel);
      --kit-panel-header-bg: linear-gradient(180deg, var(--header-hi), var(--header-lo));
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

  /** The titlebar's own severable read (`local-info.ts`'s own instance dedupes
   * on the same query key, so this is not a second poll) — `hub.reachable`
   * off `GET /api/runner`, the same fact `local-info`'s "link" cell renders. */
  private readonly runnerStatusQuery = injectRunnerStatusQuery();

  /** Whether the hub link is reachable — the titlebar's `live` dot. A
   * malformed body (e.g. a misrouted proxy) must degrade to `false`, not
   * throw mid-render — the same guard `local-info.ts`'s own `view` takes. */
  protected readonly hubReachable = computed(() => this.runnerStatusQuery.data()?.hub?.reachable ?? false);
}
