import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/** The beacon's active-state color ladder — amber for work in flight or a held
 * resource, red for an escalation, green for a live connection. A narrower vocabulary than {@link Tone} (used
 * as a CSS custom-property key, not the derived-status ladder itself). */
export type BeaconTone = 'amber' | 'red' | 'green';

/** How an active beacon animates — `pulse` fades smoothly, `step` is the hard
 * on/off blink that marks a missing machine, `hold` shows its tone steady. */
export type BeaconBlink = 'pulse' | 'step' | 'hold';

const BEACON_COLOR: Record<BeaconTone, string> = {
  amber: 'var(--amber)',
  red: 'var(--red)',
  green: 'var(--green)',
};

/**
 * The square lane-blink beacon — a small square that shows its tone for an
 * active/occupied state, animated as {@link KitBeacon.blink} says, and sits static
 * grey otherwise, honoring `prefers-reduced-motion` either way. Presentational, input-only: it holds no data client.
 */
@Component({
  selector: 'fleet-kit-beacon',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-beacon.html',
  styleUrl: './kit-beacon.css',
})
export class KitBeacon {
  /** Whether the beacon shows its tone (an occupied lane, a held environment) or
   * sits static grey (a quiet lane, an unused environment). */
  readonly active = input<boolean>(false);

  /** The color it shows while {@link active} — ignored (always the static grey)
   * otherwise. */
  readonly tone = input<BeaconTone>('amber');

  /** The animation an active beacon uses — a throb, a hard blink, or `hold` for a
   * steady light — ignored while inactive. */
  readonly blink = input<BeaconBlink>('pulse');

  protected readonly color = computed(() => (this.active() ? BEACON_COLOR[this.tone()] : 'var(--label-dim)'));
}
