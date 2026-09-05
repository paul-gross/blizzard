import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { compactRef, KitBadge, type runnerApi } from 'fleet';

import type { MachineChunkStatus } from './chunk-status';

/**
 * {@link ChunkCard}'s presentational sibling (`bzh:frontend-container-presentational`):
 * plain inputs only, injects nothing, and owns the mobile card's template — the
 * container keeps the per-row {@link "./chunk-title.query".injectChunkTitleQuery} read
 * and hands this component its resolved {@link linkedItems}.
 */
@Component({
  selector: 'local-chunk-card-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge],
  templateUrl: './chunk-card-view.html',
  styleUrl: './chunk-card-view.css',
})
export class ChunkCardView {
  /** The chunk's newest lease — the card's execution facts (node, epoch). */
  readonly lease = input.required<runnerApi.LeaseView>();

  /** The derived machine-side status, folded by the container (one owner). */
  readonly status = input.required<MachineChunkStatus>();

  /** Whether the container considers this card the current selection. */
  readonly selected = input(false);

  /** The severable work-item enrichment (issue #28, decision 1) — resolved by the
   * container's own query; empty before it resolves or when it fails. */
  readonly linkedItems = input<readonly runnerApi.WorkItemEntry[]>([]);

  /** Emits this card's `chunk_id` on click/Enter/Space — same convention as `ChunkRow`'s `selectChunk`. */
  readonly selectChunk = output<string>();

  protected onSelect(event?: Event): void {
    event?.preventDefault();
    this.selectChunk.emit(this.chunkId());
  }

  protected readonly chunkId = computed(() => this.lease().chunk_id);
  protected readonly chunkRef = computed(() => compactRef(this.chunkId()));
}
