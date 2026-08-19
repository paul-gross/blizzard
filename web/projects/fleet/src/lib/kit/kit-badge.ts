import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { Tone } from './tone';

/** The `Tone` → color ladder — the hub board's derived-status scheme. Exported
 * as {@link toneColor} so a consumer that needs the bare color (not a badge) —
 * the runner's chunk cards' lane-colored left edge, `local-panel/chunk-row.ts`
 * — derives from this one ladder instead of re-typing its own (`bzh:frontend-formatters`). */
const TONE_COLOR: Record<Tone, string> = {
  running: 'var(--amber)',
  needs: 'var(--red)',
  waiting: 'var(--amber-hi)',
  takeover: 'var(--amber-hi)',
  spawning: 'var(--cyan)',
  stale: 'var(--red)',
  done: 'var(--green)',
  idle: 'var(--label-dim)',
};

/** The design-token color a given {@link Tone} resolves to — {@link KitBadge}'s
 * own color ladder, exposed for chrome that colors by tone without rendering a
 * badge (e.g. a card's lane-colored left edge). */
export function toneColor(tone: Tone): string {
  return TONE_COLOR[tone];
}

/** The `soft` variant's muted border companion per tone (mock screen C's pill
 * vocabulary, `../../docs/designs/mobile/core-flows.html`) — each tone reuses
 * its own existing `-dim` token rather than a new color; a tone with no dim
 * companion of its own (`waiting`/`takeover`, both `amber-hi`) reuses the
 * nearest existing one instead of inventing one. */
const TONE_DIM: Record<Tone, string> = {
  running: 'var(--amber-dim)',
  needs: 'var(--red-dim)',
  waiting: 'var(--amber-dim)',
  takeover: 'var(--amber-dim)',
  spawning: 'var(--cyan-dim)',
  stale: 'var(--red-dim)',
  done: 'var(--green-dim)',
  idle: 'var(--label-dim)',
};

/**
 * The tone badge (issue #78) — a projected label colored by {@link Tone},
 * in one of three variants: plain uppercase text (`variant: 'text'`, matching
 * the derived chunk-status ladder), a bordered pill (`variant: 'pill'`), or
 * the mock's muted, fully-rounded `'soft'` pill (mock screen C) — same bright
 * tone color for the text, a `color-mix`-tinted fill and a dimmed border
 * instead of a saturated one, for a mobile row that reads as calm rather than
 * an alarm. Presentational, input-only: it owns the tone→color mapping so
 * every consumer of the same `Tone` reads identically instead of re-deriving
 * its own color per status.
 */
@Component({
  selector: 'fleet-kit-badge',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-badge.html',
  styleUrl: './kit-badge.css',
})
export class KitBadge {
  /** The status this badge colors for. */
  readonly tone = input.required<Tone>();

  /** `'text'` (default) is plain colored text, matching the derived-status
   * row ladder; `'pill'` adds a matching-color border, for a badge that reads
   * as a discrete marker rather than inline status text; `'soft'` is the
   * mock's muted, fully-rounded pill — same text color, a dimmed border and a
   * tinted fill instead of `'pill'`'s saturated `currentcolor` border. */
  readonly variant = input<'text' | 'pill' | 'soft'>('text');

  protected readonly color = computed(() => toneColor(this.tone()));

  /** The `'soft'` variant's dimmed border color. */
  protected readonly dim = computed(() => TONE_DIM[this.tone()]);

  /** The `'soft'` variant's tinted fill — a 12% mix of the tone's own bright
   * color over transparent, matching the mock's `rgba(tone, 0.12)` pills
   * without a raw literal (`bzh:frontend-kit`). */
  protected readonly softBg = computed(() => `color-mix(in srgb, ${this.color()} 12%, transparent)`);
}
