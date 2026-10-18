import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { WorkRefView } from '../../api/hub';
import { compactRef } from '../../core/compact-ref';
import { toneColor } from '../../kit/kit-badge';
import type { Tone } from '../../kit/tone';
import { LensLink } from '../../core/lens-link';
import { WorkRefLink } from '../work-ref-link/work-ref-link';

/**
 * The chunk-detail-page identity header — the chunk's own id beside its
 * derived-status badge. Presentational, no injection: an input for the id,
 * the status text, and the {@link Tone} it colors the badge by.
 *
 * Renders the **full** `ch_…` id, not `compactRef`'s short form
 * (`compact-ref.ts`). That id is a single unbroken token, so `.cid` allows a
 * mid-token break (`overflow-wrap: anywhere`, with `min-width: 0` so the flex
 * item can shrink) rather than widening a phone-width page.
 */
@Component({
  selector: 'fleet-chunk-page-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, LensLink, WorkRefLink],
  templateUrl: './chunk-page-header.html',
  styleUrl: './chunk-page-header.css',
})
export class ChunkPageHeader {
  /** The chunk's full `ch_…` id — never `compactRef`'d here. */
  readonly chunkId = input.required<string>();

  /** The chunk's current status, rendered verbatim on the badge. */
  readonly status = input.required<string>();

  /** The derived {@link Tone} the status text colors by — resolved by the caller
   * before this component ever sees the status (`bzh:frontend-formatters`). */
  readonly tone = input.required<Tone>();

  /** The work items this chunk serves, rendered first on the line. */
  readonly pointers = input<readonly WorkRefView[]>([]);

  /** Every prerequisite this chunk still waits on, as chunk ids — the whole set, not
   * `blocked`'s one representative. */
  readonly blockedBy = input<readonly string[]>([]);

  /** Every chunk this one still holds up, as chunk ids. */
  readonly blocking = input<readonly string[]>([]);

  /** The chunk detail route's own path segments, before the chunk id — this routed
   * page has no dock to select a chunk into, so its own blocked marking navigates
   * instead ({@link ChunkDetailHeader}'s own `linkBase` follows the same convention).
   * Required: each daemon mounts the page under its own board. */
  readonly linkBase = input.required<readonly string[]>();

  /** The status text's own color, resolved off {@link tone} (`kit-badge.ts` owns the
   * mapping). */
  protected readonly statusColor = computed<string>(() => toneColor(this.tone()));

  /** A neighbor's compact ref — every surface that names an entity compactly resolves
   * through {@link compactRef} (`compact-ref.ts`). */
  protected shortId(chunkId: string): string {
    return compactRef(chunkId);
  }
}
