import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { compactRef, KitBadge, toneColor, type runnerApi } from 'fleet';

import { injectChunkTitleQuery } from './chunk-title.query';
import type { MachineChunkStatus } from './chunk-status';

/**
 * One chunk on this machine — the machine-chunks list's card (issue #134):
 * a lane-colored left edge (the derived status's own {@link toneColor}, the
 * same ladder {@link KitBadge} paints its pill with — the hub board's own
 * card scheme, `fleet/board-card/board-card.ts`) over stacked lines —
 * compact chunk ref + node name/attempt epoch, one line per work item (each
 * carrying that item's own ref chip, linked when it has a `web_url`, and that
 * item's own title), then the derived status. Same fields as the row this
 * replaces, laid out like the hub board's card instead of one cramped grid
 * line.
 *
 * The work-item enrichment is the same severable, volatile layering the old lease row
 * carried (issue #28, decision 1): one {@link injectChunkTitleQuery} per row,
 * read optimistically — every degraded case (hub down, no source, per-pointer
 * forge failure) collapses to "render nothing extra". A pointer with a
 * `web_url` renders as a real link; clicking it must select nothing, so the
 * anchor stops propagation.
 */
@Component({
  selector: 'local-chunk-row',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge],
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

  protected onSelect(event?: Event): void {
    event?.preventDefault();
    this.selectChunk.emit(this.chunkId());
  }

  protected readonly chunkId = computed(() => this.lease().chunk_id);
  protected readonly chunkRef = computed(() => compactRef(this.chunkId()));

  /** The card's left edge — {@link toneColor} off the derived status, the
   * same ladder the status badge paints with, so the two never disagree. */
  protected readonly laneColor = computed(() => toneColor(this.status().tone));

  /** The severable work-item read (issue #28, decision 1) — never branched on for pending/error. */
  protected readonly titleQuery = injectChunkTitleQuery(() => this.chunkId());

  protected readonly linkedItems = computed(() => this.titleQuery.data()?.items ?? []);
}
