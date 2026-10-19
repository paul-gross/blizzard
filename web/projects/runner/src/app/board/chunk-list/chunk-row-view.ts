import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { compactRef, KitBadge, type runnerApi } from 'fleet';

import { WorkerLine } from '../../runners/worker-line';
import type { MachineChunkRow } from '../../core/chunk-rows.model';
import { showsChunkStatus } from '../app-panel.model';

/**
 * {@link ChunkRow}'s lane card (`bzh:frontend-container-presentational`): plain inputs only,
 * injects nothing — the hub board card's look, project tint included. Its work items (label
 * over title), the chunk's live worker ({@link WorkerLine}), then the step row: runner and
 * node — the last session's, once none is live — the derived status, and the short id.
 */
@Component({
  selector: 'app-chunk-row-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge, WorkerLine],
  templateUrl: './chunk-row-view.html',
  styleUrl: './chunk-row-view.css',
})
export class ChunkRowView {
  /** The chunk's folded row. */
  readonly row = input.required<MachineChunkRow>();

  /** The resolved project tint, or `null` for an untinted card. */
  readonly projectTint = input<string | null>(null);

  /** Whether this card is the current selection. */
  readonly selected = input(false);

  /** The chunk's linked work items — empty when none are known. */
  readonly linkedItems = input<readonly runnerApi.WorkItemEntry[]>([]);

  /** Emits this card's `chunk_id` on click/Enter/Space. */
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
