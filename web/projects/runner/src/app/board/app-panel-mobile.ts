import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { KitAsyncState, type KitAsyncStateValue, KitBackBar, KitButton, KitPanel, KitPanelHeader, type runnerApi } from 'fleet';

import { AgentRow } from '../machine/agent-row';
import { ChunkCard } from './chunk-list/chunk-card';
import { MachineDetail } from './chunk-dock/chunk-detail';
import type { MachineChunkStatus } from './chunk-list/chunk-status';
import { LocalAsks } from '../asks/app-asks';
import { LocalInfo } from '../machine/app-info';
import type { MachineChunkRow } from './app-panel.model';

/**
 * The runner local panel's mobile shell (mobile mockups, `../docs/designs/mobile/README.md`)
 * — a single scrolling column stacking the glance-relevant pieces in attention
 * order: machine info/status first (the hub link, `app-info`), then
 * agents/leases, then chunks on this machine, then local asks. Every section is a
 * desktop-layout component reused verbatim (`bzh:frontend-kit`) — this shell
 * only orders and stacks them, it never forks or re-styles their internals.
 *
 * Selecting a chunk (a {@link ChunkCard} or {@link AgentRow} tap) drills down
 * to a **detail screen** ({@link MachineDetail}, `bzh:frontend-kit`) in place of
 * the list; this shell only swaps which of the two screens is mounted and adds
 * the back affordance a drill-down needs.
 *
 * {@link LocalPauseControl} is not mounted here — pinned by
 * `app-panel-mobile.spec.ts`'s "mounts no local pause control or paused-by-hub
 * badge".
 *
 * Owns no titlebar; see `../shell/nav/mobile-titlebar.ts`.
 */
@Component({
  selector: 'app-panel-mobile',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    AgentRow,
    ChunkCard,
    KitAsyncState,
    KitBackBar,
    KitButton,
    KitPanel,
    KitPanelHeader,
    LocalAsks,
    LocalInfo,
    MachineDetail,
  ],
  templateUrl: './app-panel-mobile.html',
  styleUrl: './app-panel-mobile.css',
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

  /** The open-ask count for the app-asks section's header note. */
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

  /** A chunk card or agent row tap — the container writes the chunk to the URL, which opens the
   * detail screen on the next render, the same shared selection the desktop rails have. */
  readonly selectChunk = output<string>();

  /** The back affordance — the container clears the selection. */
  readonly closeDetail = output<void>();

  /** Whether the detail screen is mounted in place of the sections list. */
  protected readonly detailOpen = computed(() => this.selectedChunkLeases().length > 0);
}
