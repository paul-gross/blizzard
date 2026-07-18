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

/**
 * The tone badge (issue #78) — a projected label colored by {@link Tone},
 * either as plain uppercase text (`variant: 'text'`, matching the derived
 * chunk-status ladder) or a bordered pill (`variant: 'pill'`). Presentational,
 * input-only: it owns the tone→color mapping so every consumer of the same
 * `Tone` reads identically instead of re-deriving its own color per status.
 */
@Component({
  selector: 'fleet-kit-badge',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <span class="badge" [class.pill]="variant() === 'pill'" [style.color]="color()">
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
  `,
})
export class KitBadge {
  /** The status this badge colors for. */
  readonly tone = input.required<Tone>();

  /** `'text'` (default) is plain colored text, matching the derived-status
   * row ladder; `'pill'` adds a matching-color border, for a badge that reads
   * as a discrete marker rather than inline status text. */
  readonly variant = input<'text' | 'pill'>('text');

  protected readonly color = computed(() => TONE_COLOR[this.tone()]);
}
