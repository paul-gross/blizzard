import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/** Clamps a percentage input to `[0, 100]` — the defensive posture every kit primitive
 * takes on an input it does not itself validate (`KitSlotBar`'s `cells()` is the same
 * shape): a caller's derivation bug shows up as a pinned bar, never an overflowing one. */
function clampPct(pct: number): number {
  return Math.min(100, Math.max(0, pct));
}

/**
 * The runner registry's rate-limit pacing gauge (issue #218) — a stacked pair of
 * continuous bars for one external-subscription window (`"5h"`/`"7d"`): the top bar is
 * the harness account's reported **utilization**, the bottom bar is how far the window
 * has **elapsed** toward its own reset. Both are percentages in `[0, 100]`, clamped here
 * rather than trusted pre-clamped from the caller.
 *
 * The reading: **top bar ahead of bottom bar = on pace to exhaust this window before it
 * resets.** Top behind bottom is the comfortable case — usage is lagging the window's
 * own clock.
 *
 * Not a `KitSlotBar` variant: that primitive renders `total` discrete filled/unfilled
 * cells (an integer occupancy count); this renders two independent continuous
 * proportions with no notion of "cells" at all — a genuinely separate primitive, not a
 * skin on the same shape.
 */
@Component({
  selector: 'fleet-kit-pace-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="pace-lbl" data-testid="pace-bar-label">{{ label() }}</div>
    <div
      class="bar util"
      data-testid="pace-bar-utilization"
      role="progressbar"
      aria-valuemin="0"
      aria-valuemax="100"
      [attr.aria-valuenow]="clampedUtilization()"
      [attr.aria-label]="label() + ' utilization'"
    >
      <div class="fill" [style.width.%]="clampedUtilization()"></div>
    </div>
    <div
      class="bar elapsed"
      data-testid="pace-bar-elapsed"
      role="progressbar"
      aria-valuemin="0"
      aria-valuemax="100"
      [attr.aria-valuenow]="clampedElapsed()"
      [attr.aria-label]="label() + ' window elapsed'"
    >
      <div class="fill" [style.width.%]="clampedElapsed()"></div>
    </div>
  `,
  styles: `
    :host {
      display: block;
    }
    .pace-lbl {
      font-size: var(--fs-label);
      color: var(--label-dim);
      margin-bottom: 2px;
    }
    .bar {
      height: 6px;
      border: 1px solid var(--bezel);
      background: var(--panel-deep);
      margin-top: 2px;
    }
    .bar .fill {
      height: 100%;
    }
    .bar.util .fill {
      background: var(--amber);
    }
    .bar.elapsed .fill {
      background: var(--cyan);
    }
  `,
})
export class KitPaceBar {
  /** The window's harness-native label — `"5h"` or `"7d"` for Claude Code. */
  readonly label = input.required<string>();

  /** The harness account's reported utilization for this window, `0-100`. Clamped
   * internally — never trusted pre-clamped from the caller. */
  readonly utilizationPct = input.required<number>();

  /** How far this window has elapsed toward its own reset, `0-100` (same unit as
   * {@link utilizationPct}, chosen so the pair never silently disagrees on scale).
   * Clamped internally for the same reason. */
  readonly elapsedPct = input.required<number>();

  protected readonly clampedUtilization = computed(() => clampPct(this.utilizationPct()));
  protected readonly clampedElapsed = computed(() => clampPct(this.elapsedPct()));
}
