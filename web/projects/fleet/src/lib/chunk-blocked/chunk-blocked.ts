import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import { compactRef } from '../compact-ref';
import { KitBadge } from '../kit/kit-badge';

/**
 * The blocked marking (issue #461) — a chunk's `BlockedView`, rendered beside
 * its unchanged status wherever a chunk is listed: the board card, the dock
 * header, and the routed chunk page header. One component rather than three
 * copies (D1): the three sites share no status component today, so this is
 * the shared piece, its chrome from {@link KitBadge} on the existing
 * `waiting` tone (D2 — blocked is not a status and never widens `Tone`).
 *
 * Two render modes, matched to what each site can already do (D3), decided by
 * {@link asLink} — a decision independent of {@link linkBase}, which only ever
 * carries the route's path segments (round 3 F6): `false` (the default) is the
 * button that emits {@link selectChunk} with the prerequisite id — the board
 * card and the dock header both already select a chunk into the dock this way,
 * one hop rather than a navigation. `true` renders a `routerLink` under
 * `linkBase` instead — the routed chunk page has no dock to select into, so it
 * navigates the way `ChunkDetailHeader`'s own identity link already does.
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
   * one-hop dock-select button — set only by a caller with no dock to select
   * into (`ChunkPageHeader`, round 3 F6: this was previously encoded by
   * `linkBase` being non-null, overloading a name every other caller in this
   * suite uses to mean only the route address). */
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
