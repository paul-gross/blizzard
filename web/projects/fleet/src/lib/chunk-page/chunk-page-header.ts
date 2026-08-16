import { ChangeDetectionStrategy, Component, input } from '@angular/core';

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
  imports: [KitBadge],
  template: `
    <header class="cp-hdr">
      <span class="cid" data-testid="mobile-chunk-ref">{{ chunkId() }}</span>
      <fleet-kit-badge [tone]="tone()" variant="soft" data-testid="mobile-chunk-status">{{ status() }}</fleet-kit-badge>
    </header>
  `,
  styles: `
    :host {
      display: block;
      flex: none;
      /* Pinned rather than inherited: the hub's and the runner's own page
         roots don't agree on an ambient font-size (the hub's own \`:host\`
         sets \`--fs-base\`; the runner's never did), and \`.badge.soft\`'s
         \`font-size: 0.78em\` (kit-badge.ts) resolves off whatever's
         inherited — so composing this header on the two pages rendered its
         badge at two different sizes until this was pinned here. */
      font-family: var(--mono);
      font-size: var(--fs-base);
      font-variant-numeric: tabular-nums;
      color: var(--text);
    }
    .cp-hdr {
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 8px;
      margin: 8px;
      padding: 0 8px;
    }
    .cid {
      color: var(--amber);
      font-size: var(--fs-md);
      min-width: 0;
      overflow-wrap: anywhere;
    }
  `,
})
export class ChunkPageHeader {
  /** The chunk's full `ch_…` id — never `compactRef`'d here. */
  readonly chunkId = input.required<string>();

  /** The chunk's current status, rendered verbatim on the badge. */
  readonly status = input.required<string>();

  /** The derived {@link Tone} the badge colors by — each caller's own
   * `STATUS_TONE`/`deriveMachineChunkStatus` fold (`bzh:frontend-formatters`). */
  readonly tone = input.required<Tone>();
}
