import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import { compactRef } from '../compact-ref';
import { KitBadge } from '../kit/kit-badge';

/**
 * The blocked marking — a chunk's `BlockedView`, rendered as a {@link KitBadge} on the
 * `waiting` tone (blocked is not a status and never widens `Tone`).
 *
 * Two render modes, decided by {@link asLink}: `false` (the default) is a button that
 * emits {@link selectChunk} with the prerequisite id; `true` renders a `routerLink`
 * under {@link linkBase}, which carries only the route's path segments.
 */
@Component({
  selector: 'fleet-chunk-blocked',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge, RouterLink],
  templateUrl: './chunk-blocked.html',
  styleUrl: './chunk-blocked.css',
})
export class ChunkBlocked {
  /** The unmet prerequisite's chunk id (`BlockedView.prerequisite_chunk_id`). */
  readonly prerequisiteChunkId = input.required<string>();

  /** The chunk detail route's own path segments, before the chunk id — the same
   * non-nullable, default-carrying contract `ChunkArtifacts`/`ChunkTimeline`/
   * `ChunkDetailHeader`/`ChunkPageHeader` all share. Only read when {@link asLink}
   * is `true`; otherwise unused. */
  readonly linkBase = input<readonly string[]>(['/board', 'chunk']);

  /** Whether to render a `routerLink` under {@link linkBase} instead of the
   * one-hop dock-select button, for a host with no dock to select into. */
  readonly asLink = input(false);

  /** Emitted with the prerequisite's chunk id when the dock-select button is
   * clicked (`linkBase` is `null`) — the caller selects it into the dock the
   * same way its own card/header click already does. */
  readonly selectChunk = output<string>();

  /** The prerequisite's compact ref — every surface that names an entity
   * compactly resolves through {@link compactRef}, and this marking is no
   * different (`compact-ref.ts`). */
  protected readonly shortId = computed(() => compactRef(this.prerequisiteChunkId()));
}
