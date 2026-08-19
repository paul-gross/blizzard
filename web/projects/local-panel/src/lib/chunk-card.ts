import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { compactRef, KitBadge, type runnerApi } from 'fleet';

import { injectChunkTitleQuery } from './chunk-title.query';
import type { MachineChunkStatus } from './chunk-status';

/**
 * One chunk on this machine — the mobile stack's own card, distinct from
 * {@link ChunkRow}'s desktop card (`bzh:frontend-kit`'s "adaptive shells over
 * shared guts"): the same inputs, the same per-row
 * {@link injectChunkTitleQuery} work-item enrichment, and the same select
 * output/keyboard affordances, laid out for a 390px viewport instead of a
 * 340px desktop column — the status renders as a soft pill beside the ref
 * rather than its own row, and the title wraps to two lines instead of
 * ellipsizing, both trading the desktop card's density for room a touch
 * target needs.
 *
 * Line 1 is the compact ref plus the derived status as a soft pill
 * (mock screen C's pill vocabulary,
 * `../../../docs/designs/mobile/core-flows.html`), right-aligned. Line 2 is
 * one work-item line per item — that item's own chip plus its own title —
 * each clamped to two lines rather than ellipsized, since a mobile card has
 * the vertical room a desktop row doesn't. Line 3 is the node + attempt
 * epoch, in the same quiet label tone {@link ChunkRow}'s own `.node` cell
 * uses.
 *
 * A tap emits `selectChunk`, which `LocalPanelMobile` drills down on — the
 * card's own `selected` state stays unbound there, since the mobile shell
 * replaces the list with the detail screen rather than showing both at once.
 */
@Component({
  selector: 'local-chunk-card',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge],
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

  protected onSelect(event?: Event): void {
    event?.preventDefault();
    this.selectChunk.emit(this.chunkId());
  }

  protected readonly chunkId = computed(() => this.lease().chunk_id);
  protected readonly chunkRef = computed(() => compactRef(this.chunkId()));

  /** The severable work-item read (issue #28, decision 1) — never branched on for pending/error. */
  protected readonly titleQuery = injectChunkTitleQuery(() => this.chunkId());

  protected readonly linkedItems = computed(() => this.titleQuery.data()?.items ?? []);
}
