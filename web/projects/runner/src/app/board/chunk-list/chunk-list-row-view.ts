import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { compactRef, KitBadge, type runnerApi } from 'fleet';

import { WorkerLine } from '../../machine/worker-line';
import { type MachineChunkRow, showsChunkStatus } from '../app-panel.model';

/**
 * {@link ChunkRow}'s list row (`bzh:frontend-container-presentational`): plain inputs only,
 * injects nothing — the hub board list row's look, project tint included. The work (short id
 * over its work items), the merged runner · step · worker cell, and the derived status.
 */
@Component({
  selector: 'app-chunk-list-row-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge, WorkerLine],
  templateUrl: './chunk-list-row-view.html',
  styleUrl: './chunk-list-row-view.css',
})
export class ChunkListRowView {
  /** The chunk's folded row. */
  readonly row = input.required<MachineChunkRow>();

  /** The resolved project tint, or `null` for an untinted row. */
  readonly projectTint = input<string | null>(null);

  /** Whether this row is the current selection. */
  readonly selected = input(false);

  /** The chunk's linked work items — empty when none are known. */
  readonly linkedItems = input<readonly runnerApi.WorkItemEntry[]>([]);

  /** Emits this row's `chunk_id` on click/Enter/Space. */
  readonly selectChunk = output<string>();

  protected onSelect(event?: Event): void {
    event?.preventDefault();
    this.selectChunk.emit(this.chunkId());
  }

  protected readonly chunkId = computed(() => this.row().lease.chunk_id);
  /** Whether the step row names the derived status — always once no worker is live, else only
   * when a human fact outranks the worker's own state, which its line already shows. */
  protected readonly showsStatus = computed(() => showsChunkStatus(this.row()));

  protected readonly chunkRef = computed(() => compactRef(this.chunkId()));
}
