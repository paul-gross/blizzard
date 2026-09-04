import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { ChunkDetail, ChunkNeighborView } from '../api/hub';
import { STATUS_TONE } from '../chunk-lanes';
import { compactRef } from '../compact-ref';
import { KitBadge } from '../kit/kit-badge';
import type { Tone } from '../kit/tone';

/** One rendered group of {@link ChunkNeighborhood.groups} — `prerequisites` or
 * `dependents`, each its own section with its own empty state. */
interface NeighborGroup {
  readonly label: string;
  readonly testid: string;
  readonly list: readonly ChunkNeighborView[];
}

/**
 * A chunk's standing dependency edges one hop each way (issue #462) —
 * `ChunkDetail.neighborhood`, rendered read-only on the chunk detail surface:
 * `prerequisites` above `dependents`. Presentational only, `bzh:frontend-
 * container-presentational` — no control here declares, releases, or edits an
 * edge; those levers stay exactly where `ChunkDetailHeader` already holds
 * them (D1).
 *
 * Each row's own {@link KitBadge} tone is driven by the edge's `satisfied`
 * flag, not the neighbor's own derived status — `done` (green) when
 * satisfied, `waiting` (amber-hi) when not, `ChunkBlocked`'s own
 * machine-parked reuse of `waiting` — so satisfaction reads as a genuine
 * computed-style difference rather than a class name a real-browser proof
 * could not see. The neighbor's own status still renders as the badge's text,
 * `STATUS_TONE`'s own vocabulary, so a resolved neighbor and an unresolvable
 * one (`status: null`, D4) read distinctly too.
 *
 * Reaching a neighbor mirrors {@link ChunkBlocked}'s own `linkBase`/`asLink`/
 * `selectChunk` shape: the dock's one-hop select-into-dock button (the
 * default) or, for a caller with no dock to select into, a `routerLink` under
 * `linkBase`.
 *
 * Deliberately alongside {@link ChunkBlocked} in the dock, not a replacement for it:
 * that marking is a compact, always-visible alert naming one earliest-unmet
 * prerequisite, confined to the pre-claim window `blocked` itself is; this panel is
 * the full one-hop map, both directions, at any status — a chunk mid-run still shows
 * what it depends on and what depends on it, which `blocked` never renders at all.
 */
@Component({
  selector: 'fleet-chunk-neighborhood',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge, NgTemplateOutlet, RouterLink],
  templateUrl: './chunk-neighborhood.html',
  styleUrl: './chunk-neighborhood.css',
})
export class ChunkNeighborhood {
  /** The chunk aggregate to render (its own one-hop-each-way edges), the same
   * whole-`ChunkDetail` input every `chunk-detail/` sibling takes. */
  readonly detail = input.required<ChunkDetail>();

  /** The chunk detail route's own path segments, before the chunk id. Only
   * read when {@link asLink} is `true`; otherwise unused. */
  readonly linkBase = input<readonly string[]>(['/board', 'chunk']);

  /** Whether to render a `routerLink` under {@link linkBase} for each
   * neighbor instead of the one-hop dock-select button. */
  readonly asLink = input(false);

  /** Emitted with a neighbor's chunk id when its dock-select button is
   * clicked (`asLink` is `false`). */
  readonly selectChunk = output<string>();

  /** `ChunkDetail.neighborhood`'s two directions as one rendered list, each with its own
   * label and empty state — `?? []` at the read site the same way every other optional
   * list field on `ChunkDetail` is handled (`chunk-awaiting-human.ts`'s `openQuestions`). */
  protected readonly groups = computed<readonly NeighborGroup[]>(() => {
    const neighborhood = this.detail().neighborhood;
    return [
      { label: 'Depends on', testid: 'neighborhood-prerequisites', list: neighborhood?.prerequisites ?? [] },
      { label: 'Blocks', testid: 'neighborhood-dependents', list: neighborhood?.dependents ?? [] },
    ];
  });

  /** Every neighbor's compact ref — every surface that names an entity
   * compactly resolves through {@link compactRef} (`compact-ref.ts`). */
  protected shortId(chunkId: string): string {
    return compactRef(chunkId);
  }

  /** The neighbor's own derived status, or `unknown` for the residual race a
   * neighbor's facts fail to resolve (D4, `status: null`). */
  protected statusLabel(neighbor: ChunkNeighborView): string {
    return neighbor.status ?? 'unknown';
  }

  /** The neighbor's own status tone, for the row text — `idle` for the
   * unresolvable case, mirroring an unclaimed chunk's own tone. */
  protected statusTone(neighbor: ChunkNeighborView): Tone {
    return neighbor.status ? STATUS_TONE[neighbor.status] : 'idle';
  }

  /** The edge's own satisfaction tone — `done`/`waiting`, never the
   * neighbor's derived status (D4: a dependent edge's satisfaction reads the
   * *subject* chunk, not the dependent neighbor's own status, so every row in
   * that list would otherwise share one status-derived tone regardless). */
  protected satisfiedTone(neighbor: ChunkNeighborView): Tone {
    return neighbor.satisfied ? 'done' : 'waiting';
  }
}
