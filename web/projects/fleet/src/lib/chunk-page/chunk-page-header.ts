import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { ChunkBlocked } from '../chunk-blocked';
import { KitBadge } from '../kit/kit-badge';
import type { Tone } from '../kit/tone';

/**
 * The chunk-detail-page identity header — the chunk's own id beside its
 * derived-status badge, lifted from the hub's `chunk-page.ts` `.cp-hdr`
 * verbatim (the layout the user approved) and shared with the runner's own
 * page, which never had one at all. Presentational, no injection: an input
 * for the id, the status text, and the {@link Tone} it colors the badge by —
 * every caller already derives that triad off its own detail read.
 *
 * Renders the **full** `ch_…` id, not `compactRef`'s short form
 * (`compact-ref.ts`) — a board card or a rail row still wants the compact
 * ref for a dense list, but a page whose whole job is naming *this one*
 * chunk reads better in full. That id is a single unbroken token far wider
 * than the short ref this header used to carry, so `.cid` allows a mid-token
 * break (`overflow-wrap: anywhere`, with `min-width: 0` so the flex item can
 * actually shrink to less than its content's width) rather than pushing a
 * phone-width page wider than its viewport — the shell sweeps
 * (`chunk-page-layout.shell-sweep.spec.ts`,
 * `chunk-detail-page.shell-sweep.spec.ts`) assert that at 320px.
 */
@Component({
  selector: 'fleet-chunk-page-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkBlocked, KitBadge],
  templateUrl: './chunk-page-header.html',
  styleUrl: './chunk-page-header.css',
})
export class ChunkPageHeader {
  /** The chunk's full `ch_…` id — never `compactRef`'d here. */
  readonly chunkId = input.required<string>();

  /** The chunk's current status, rendered verbatim on the badge. */
  readonly status = input.required<string>();

  /** The derived {@link Tone} the badge colors by — each caller's own
   * `STATUS_TONE`/`deriveMachineChunkStatus` fold (`bzh:frontend-formatters`). */
  readonly tone = input.required<Tone>();

  /** The unmet prerequisite's chunk id, from `ChunkDetail.blocked` (issue #461) — null
   * when the chunk carries no marking. */
  readonly blockedOn = input<string | null>(null);

  /** The chunk detail route's own path segments, before the chunk id — this routed
   * page has no dock to select a chunk into, so its own blocked marking navigates
   * instead ({@link ChunkDetailHeader}'s own `linkBase` follows the same convention). */
  readonly linkBase = input<readonly string[]>(['/board', 'chunk']);
}
