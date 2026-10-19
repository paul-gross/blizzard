import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { type KitAsyncStateValue, KitBackBar, KitButton, type runnerApi } from 'fleet';

import { type BoardLayout, ChunkBoard } from './chunk-list/chunk-board';
import { MachineDetail } from './chunk-dock/chunk-detail';
import type { MachineChunkStatus } from './chunk-list/chunk-status';
import { BoardRail } from './rail/board-rail';
import type { MachineChunkRow } from './app-panel.model';
import type { PanelLease, RunnerOwner } from '../core/panel-reads.model';

/**
 * The runner local panel's mobile shell (mobile mockups, `../docs/designs/mobile/README.md`)
 * — a single scrolling column stacking the glance-relevant pieces in attention
 * order: the chunks first ({@link ChunkBoard}, its lanes stacked, each card carrying its
 * live worker), then the rail ({@link BoardRail} — the hub link, this host, the local asks). Every section is a
 * desktop-layout component reused verbatim (`bzh:frontend-kit`) — this shell
 * only orders and stacks them, it never forks or re-styles their internals.
 *
 * Selecting a chunk (a card or row tap) drills down
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
    ChunkBoard,
    KitBackBar,
    KitButton,
    BoardRail,
    MachineDetail,
  ],
  templateUrl: './app-panel-mobile.html',
  styleUrl: './app-panel-mobile.css',
})
export class LocalPanelMobile {
  /** The chunks section's async triad state. */
  readonly chunksTriadState = input.required<KitAsyncStateValue>();

  /** One row per chunk on this machine, pre-folded by the container. */
  readonly machineChunks = input.required<readonly MachineChunkRow[]>();

  /** Which layout the chunks render in — the lanes stacked, or the ranked list. */
  readonly layout = input<BoardLayout>('board');

  /** The open-ask count for the app-asks section's header note. */
  readonly openAskCount = input.required<number>();

  /** The runners the board reads — the hub-link rail shows each. */
  readonly runners = input.required<readonly RunnerOwner[]>();

  /** The selected chunk's attempts (oldest → newest), resolved by the
   * container off the URL's `chunk` param — the detail screen's subject.
   * Empty when nothing is selected, which is also what keeps the shell on
   * the list: a `chunk` naming something not on this machine resolves to no
   * attempts, so the drill-down degrades to the list rather than an empty
   * screen. */
  readonly selectedChunkLeases = input<readonly PanelLease[]>([]);

  /** The selected chunk's derived machine-side status (container-folded). */
  readonly selectedStatus = input<MachineChunkStatus | null>(null);

  /** The selected chunk's open escalation, when there is one. */
  readonly selectedEscalation = input<runnerApi.EscalationView | null>(null);

  /** Emitted with the layout the Board/List switch was set to. */
  readonly layoutChange = output<BoardLayout>();

  /** A chunk card or row tap — the container writes the chunk to the URL, which opens the
   * detail screen on the next render, the same shared selection the desktop rails have. */
  readonly selectChunk = output<string>();

  /** The back affordance — the container clears the selection. */
  readonly closeDetail = output<void>();

  /** Whether the detail screen is mounted in place of the sections list. */
  protected readonly detailOpen = computed(() => this.selectedChunkLeases().length > 0);
}
