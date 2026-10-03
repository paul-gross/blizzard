import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { compactRef, KitBadge, type runnerApi } from 'fleet';

import type { MachineChunkStatus } from './chunk-status';

/**
 * {@link ChunkCard}'s presentational sibling (`bzh:frontend-container-presentational`):
 * plain inputs only, injects nothing, and owns the mobile card's template.
 */
@Component({
  selector: 'app-chunk-card-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge],
  templateUrl: './chunk-card-view.html',
  styleUrl: './chunk-card-view.css',
})
export class ChunkCardView {
  /** The chunk's newest lease — the card's execution facts (node, epoch). */
  readonly lease = input.required<runnerApi.LeaseView>();

  /** The derived machine-side status. */
  readonly status = input.required<MachineChunkStatus>();

  /** Whether this card is the current selection. */
  readonly selected = input(false);

  /** The chunk's linked work items — empty when none are known. */
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
