import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { ChunkCard, ChunkListRow } from 'fleet';

import type { MachineChunkRow } from '../../core/chunk-rows.model';
import { WorkerLine } from '../../runners/worker-line';
import { machineChunkCardView } from './chunk-card-view.model';
import { injectChunkTitleQuery } from './chunk-title.query';

/**
 * One chunk on the runners read — the fleet card's and list row's **container**: owns the per-row
 * {@link injectChunkTitleQuery} work-item enrichment read and maps the folded row into the fleet view
 * ({@link machineChunkCardView}), handing it to the presentational {@link ChunkCard} (the lane card) or
 * {@link ChunkListRow} (the list row), with the chunk's live {@link WorkerLine} in the worker slot
 * (`bzh:frontend-container-presentational`).
 *
 * The work-item enrichment is severable, volatile layering: one {@link injectChunkTitleQuery} per row, read
 * optimistically — every degraded case (hub down, no source, per-pointer forge failure) collapses to "render nothing
 * extra".
 */
@Component({
  selector: 'app-chunk-row',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkCard, ChunkListRow, WorkerLine],
  templateUrl: './chunk-row.html',
  styleUrl: './chunk-row.css',
})
export class ChunkRow {
  /** The chunk's folded row — its newest lease, live worker, status, lane and project. */
  readonly row = input.required<MachineChunkRow>();

  /** Which presentation renders — a lane card or a list row. */
  readonly layout = input<'board' | 'list'>('board');

  /** Whether the container considers this row the current selection. */
  readonly selected = input(false);

  /** Emits this row's `chunk_id` on activation. */
  readonly selectChunk = output<string>();

  /** The severable work-item read — never branched on for pending/error. */
  protected readonly titleQuery = injectChunkTitleQuery(
    () => this.row().lease.owner.runnerId,
    () => this.row().lease.chunk_id,
  );

  protected readonly view = computed(() => machineChunkCardView(this.row(), this.titleQuery.data()?.items ?? []));
}
