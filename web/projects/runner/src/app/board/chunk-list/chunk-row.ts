import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { projectColorValue } from 'fleet';

import type { MachineChunkRow } from '../app-panel.model';
import { injectChunkTitleQuery } from './chunk-title.query';
import { ChunkListRowView } from './chunk-list-row-view';
import { ChunkRowView } from './chunk-row-view';

/**
 * One chunk on the runners read — the board card's and list row's **container**: owns the
 * per-row {@link injectChunkTitleQuery} work-item enrichment read and resolves the project
 * tint, handing both to the presentational {@link ChunkRowView} (the lane card) or
 * {@link ChunkListRowView} (the list row), which own the markup
 * (`bzh:frontend-container-presentational`).
 *
 * The work-item enrichment is severable, volatile layering: one
 * {@link injectChunkTitleQuery} per row, read optimistically — every degraded case (hub down, no source, per-pointer
 * forge failure) collapses to "render nothing extra".
 */
@Component({
  selector: 'app-chunk-row',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkListRowView, ChunkRowView],
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

  /** Emits this row's `chunk_id` on click/Enter/Space. */
  readonly selectChunk = output<string>();

  protected readonly chunkId = computed(() => this.row().lease.chunk_id);

  /** The project colour the background is tinted with, or `null` for an untinted row. */
  protected readonly projectTint = computed(() => {
    const color = this.row().projectColor;
    return color ? projectColorValue(color) : null;
  });

  /** The severable work-item read — never branched on for pending/error. */
  protected readonly titleQuery = injectChunkTitleQuery(
    () => this.row().lease.owner.runnerId,
    () => this.chunkId(),
  );

  protected readonly linkedItems = computed(() => this.titleQuery.data()?.items ?? []);
}
