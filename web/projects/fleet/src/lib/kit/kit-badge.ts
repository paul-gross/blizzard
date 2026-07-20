import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { Tone } from './tone';

/** The `Tone` → color ladder — the hub board's derived-status scheme,
 * duplicated today across `chunk-row.ts` and the board's own lane coloring. */
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
  template: `
    <span
      class="badge"
      [class.pill]="variant() === 'pill'"
      [class.soft]="variant() === 'soft'"
      [style.color]="color()"
      [style.border-color]="variant() === 'soft' ? dim() : null"
      [style.background]="variant() === 'soft' ? softBg() : null"
    >
      <ng-content />
    </span>
  `,
  styles: `
    :host {
      display: inline;
    }
    .badge {
      font-size: var(--fs-label);
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }
    .badge.pill {
      border: 1px solid currentcolor;
      padding: 0 4px;
      font-size: 0.85em;
      letter-spacing: 0.08em;
    }
    /* The mock's thin, fully-rounded pill (screen C) — border-color/background
       come from the tone-derived style bindings above, not from here. */
    .badge.soft {
      display: inline-block;
      border: 1px solid transparent;
      border-radius: 999px;
      padding: 1px 8px;
      font-size: 0.78em;
      font-weight: 400;
      letter-spacing: 0.08em;
    }
  `,
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

  protected readonly color = computed(() => TONE_COLOR[this.tone()]);

  /** The `'soft'` variant's dimmed border color. */
  protected readonly dim = computed(() => TONE_DIM[this.tone()]);

  /** The `'soft'` variant's tinted fill — a 12% mix of the tone's own bright
   * color over transparent, matching the mock's `rgba(tone, 0.12)` pills
   * without a raw literal (`bzh:frontend-kit`). */
  protected readonly softBg = computed(() => `color-mix(in srgb, ${this.color()} 12%, transparent)`);
}
