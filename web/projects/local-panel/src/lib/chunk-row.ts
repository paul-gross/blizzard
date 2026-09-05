import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import type { runnerApi } from 'fleet';

import { injectChunkTitleQuery } from './chunk-title.query';
import { ChunkRowView } from './chunk-row-view';
import type { MachineChunkStatus } from './chunk-status';

/**
 * One chunk on this machine — the machine-chunks list's card (issue #134)
 * **container**: owns the per-row {@link injectChunkTitleQuery} work-item
 * enrichment read and hands its resolved items to the presentational
 * {@link ChunkRowView}, which owns the actual card markup
 * (`bzh:frontend-container-presentational`).
 *
 * The work-item enrichment is the same severable, volatile layering the old lease row
 * carried (issue #28, decision 1): one {@link injectChunkTitleQuery} per row,
 * read optimistically — every degraded case (hub down, no source, per-pointer
 * forge failure) collapses to "render nothing extra".
 */
@Component({
  selector: 'local-chunk-row',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkRowView],
  templateUrl: './chunk-row.html',
  styleUrl: './chunk-row.css',
})
export class ChunkRow {
  /** The chunk's newest lease — the row's execution facts (node, epoch). */
  readonly lease = input.required<runnerApi.LeaseView>();

  /** The derived machine-side status, folded by the container (one owner). */
  readonly status = input.required<MachineChunkStatus>();

  /** Whether the container considers this row the current selection. */
  readonly selected = input(false);

  /** Emits this row's `chunk_id` on click/Enter/Space — same convention as `selectLease`. */
  readonly selectChunk = output<string>();

  protected readonly chunkId = computed(() => this.lease().chunk_id);

  /** The severable work-item read (issue #28, decision 1) — never branched on for pending/error. */
  protected readonly titleQuery = injectChunkTitleQuery(() => this.chunkId());

  protected readonly linkedItems = computed(() => this.titleQuery.data()?.items ?? []);
}
