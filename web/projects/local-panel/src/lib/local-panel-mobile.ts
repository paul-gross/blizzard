import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { KitAsyncState, type KitAsyncStateValue, KitBackBar, KitPanel, KitPanelHeader, type runnerApi } from 'fleet';

import { AgentRow } from './agent-row';
import { ChunkCard } from './chunk-card';
import { MachineDetail } from './chunk-detail';
import type { MachineChunkStatus } from './chunk-status';
import { LocalAsks } from './local-asks';
import { LocalInfo } from './local-info';
import type { MachineChunkRow } from './local-panel';

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
 * Owns no titlebar (issue #325): the shared `MobileTitlebar` chrome — its
 * live dot, its overflow menu, and the signed-in identity/logout row inside
 * that menu — moved up to the app root's own `app-mobile-titlebar`
 * (`../../runner/src/app/nav/mobile-titlebar.ts`'s `MobileTitlebar`), the
 * same shelf the hub's app-root mounts its own mobile titlebar on. Mounted
 * once, above the routed content, rather than nested inside this routed
 * shell where it used to render only on `/board` and not at all on
 * `/events`.
 */
@Component({
  selector: 'local-panel-mobile',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [AgentRow, ChunkCard, KitAsyncState, KitBackBar, KitPanel, KitPanelHeader, LocalAsks, LocalInfo, MachineDetail],
  templateUrl: './local-panel-mobile.html',
  styleUrl: './local-panel-mobile.css',
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
}
