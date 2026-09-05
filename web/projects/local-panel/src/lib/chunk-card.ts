import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import type { runnerApi } from 'fleet';

import { injectChunkTitleQuery } from './chunk-title.query';
import { ChunkCardView } from './chunk-card-view';
import type { MachineChunkStatus } from './chunk-status';

/**
 * One chunk on this machine — the mobile stack's own card (issue #134)
 * **container**: owns the per-row {@link injectChunkTitleQuery} work-item
 * enrichment read and hands its resolved items to the presentational
 * {@link ChunkCardView}, which owns the actual card markup
 * (`bzh:frontend-container-presentational`), distinct from {@link "./chunk-row".ChunkRow}'s
 * desktop card (`bzh:frontend-kit`'s "adaptive shells over shared guts").
 */
@Component({
  selector: 'local-chunk-card',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkCardView],
  templateUrl: './chunk-card.html',
  styleUrl: './chunk-card.css',
})
export class ChunkCard {
  /** The chunk's newest lease — the card's execution facts (node, epoch). */
  readonly lease = input.required<runnerApi.LeaseView>();

  /** The derived machine-side status, folded by the container (one owner). */
  readonly status = input.required<MachineChunkStatus>();

  /** Whether the container considers this card the current selection. */
  readonly selected = input(false);

  /** Emits this card's `chunk_id` on click/Enter/Space — same convention as `ChunkRow`'s `selectChunk`. */
  readonly selectChunk = output<string>();

  protected readonly chunkId = computed(() => this.lease().chunk_id);

  /** The severable work-item read (issue #28, decision 1) — never branched on for pending/error. */
  protected readonly titleQuery = injectChunkTitleQuery(() => this.chunkId());

  protected readonly linkedItems = computed(() => this.titleQuery.data()?.items ?? []);
}
