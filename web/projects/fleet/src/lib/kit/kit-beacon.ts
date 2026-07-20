import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/** The beacon's active-state color ladder — amber for work in flight or a held
 * resource, red for an escalation. A narrower vocabulary than {@link Tone} (used
 * as a CSS custom-property key, not the derived-status ladder itself): the two
 * consumers today (the board's occupied-lane header, the environments rail's
 * held indicator) only ever need one of these two. */
export type BeaconTone = 'amber' | 'red';

const BEACON_COLOR: Record<BeaconTone, string> = {
  amber: 'var(--amber)',
  red: 'var(--red)',
};

/**
 * The square lane-blink beacon (issue #106) — a small square that throbs for an
 * active/occupied state and sits static grey otherwise, honoring
 * `prefers-reduced-motion` either way. Extracted from the board's occupied-lane
 * header indicator (`board-shell.ts`) and the environments rail's held indicator
 * (`local-panel`'s `env-list.ts`), which had each retyped the same
 * `.blink`/`@keyframes` chrome — `bzh:frontend-kit-floor` requires presentational
 * chrome like this come from the kit once, not a re-typed copy per consumer.
 * Presentational, input-only: it holds no data client.
 */
@Component({
  selector: 'fleet-kit-beacon',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="beacon" [class.active]="active()" [style.background]="color()"></span>`,
  styles: `
    :host {
      display: inline-block;
    }
    .beacon {
      display: block;
      width: 7px;
      height: 7px;
    }
    .beacon.active {
      animation: beacon-blink 2s ease-in-out infinite;
    }
    @keyframes beacon-blink {
      50% {
        opacity: 0;
      }
    }
    @media (prefers-reduced-motion: reduce) {
      .beacon.active {
        animation: none;
      }
    }
  `,
})
export class KitBeacon {
  /** Whether the beacon throbs (an occupied lane, a held environment) or sits
   * static grey (a quiet lane, an unused environment). */
  readonly active = input<boolean>(false);

  /** The color it throbs while {@link active} — ignored (always the static grey)
   * otherwise. */
  readonly tone = input<BeaconTone>('amber');

  protected readonly color = computed(() => (this.active() ? BEACON_COLOR[this.tone()] : 'var(--label-dim)'));
}
