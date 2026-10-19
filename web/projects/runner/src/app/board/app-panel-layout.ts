import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { type runnerApi, type KitAsyncStateValue } from 'fleet';

import { MachineDetail } from './chunk-dock/chunk-detail';
import { type BoardLayout, ChunkBoard } from './chunk-list/chunk-board';
import type { MachineChunkStatus } from './chunk-list/chunk-status';
import { BoardRail } from './rail/board-rail';
import type { MachineChunkRow } from './app-panel.model';
import type { PanelLease } from '../core/panel-reads.model';

/**
 * The runner's machine-local panel's layout half — shaped like
 * the discovery mock's machine panel: a two-column grid over the runner's
 * local API, hub-free save for the rails that proxy through it — the
 * hub panel's fleet counts.
 *
 * - **Center (1fr)** — work: the chunks on these runners as the hub's board lays them out
 *   ({@link ChunkBoard} — lanes or list, one card per chunk, its live worker inline) over
 *   the detail dock for the selected chunk, which takes no room while nothing is selected.
 * - **Right (330px)** — the rail ({@link BoardRail}): the hub link, this host, and the
 *   open local asks.
 *
 * Presentational only: it renders exactly the chunks/selection it is
 * handed and emits `selectChunk`. Color
 * resolves through the shared design tokens (`fleet` library,
 * design/tokens.css), never hard-coded hex.
 *
 * Owns no titlebar; see `../shell/nav/app-header.ts`.
 */
@Component({
  selector: 'app-panel-layout',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ChunkBoard,
    MachineDetail,
    BoardRail,
  ],
  templateUrl: './app-panel-layout.html',
  styleUrl: './app-panel-layout.css',
})
export class LocalPanelLayout {
  /** The machine-chunks list's async triad state. */
  readonly chunksTriadState = input.required<KitAsyncStateValue>();

  /** One row per chunk on this machine, pre-folded by the container — the container
   * owns the fold, this renders exactly the rows it is handed. */
  readonly machineChunks = input.required<readonly MachineChunkRow[]>();

  /** Which layout the chunks render in — the lane board or the ranked list. */
  readonly layout = input<BoardLayout>('board');

  /** The open-ask count for the asks panel's header note. */
  readonly openAskCount = input.required<number>();


  /** The `chunk_id` currently selected, or `null`. */
  readonly selectedChunkId = input.required<string | null>();

  /** The selected chunk's attempts (oldest → newest). */
  readonly selectedChunkLeases = input.required<readonly PanelLease[]>();

  readonly selectedStatus = input.required<MachineChunkStatus | null>();

  /** The open escalation for the selected chunk, when one exists. */
  readonly selectedEscalation = input.required<runnerApi.EscalationView | null>();

  /** Emitted with a chunk id when the operator selects a chunk card or row. */
  readonly selectChunk = output<string>();

  /** Emitted with the layout the Board/List switch was set to. */
  readonly layoutChange = output<BoardLayout>();

  /** Emitted when the operator dismisses the detail dock via its
   * own close button — the container clears the selection. */
  readonly dismiss = output<void>();
}
