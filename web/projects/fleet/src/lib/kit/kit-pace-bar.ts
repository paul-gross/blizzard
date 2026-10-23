import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { DENSE_AFTER } from './kit-slot-bar';

/** How many blocks the `blocks` variant draws — one per tenth of the window. */
export const PACE_BLOCKS = 10;

/** How a pace bar draws its window: `bars` as a stacked pair of continuous bars, `blocks` as one compact line of blocks. */
export type PaceBarVariant = 'bars' | 'blocks';

/** One block of the `blocks` variant: filled to the window's usage, and marked where its clock stands. */
interface PaceBlock {
  readonly on: boolean;
  /** The clock sits on this block's left edge — or, on the last block, `clockAfter` its right. */
  readonly clockBefore: boolean;
  readonly clockAfter: boolean;
}

/** Clamps a percentage input to `[0, 100]` — the defensive posture every kit primitive
 * takes on an input it does not itself validate (`KitSlotBar`'s `cells()` is the same
 * shape): a caller's derivation bug shows up as a pinned bar, never an overflowing one. */
function clampPct(pct: number): number {
  return Math.min(100, Math.max(0, pct));
}

function toTenths(clampedPct: number): number {
  return Math.round(clampedPct / (100 / PACE_BLOCKS));
}

/**
 * The runner registry's rate-limit pacing gauge — a stacked pair of
 * continuous bars for one external-subscription window (`"5h"`/`"7d"`): the top bar is
 * the harness account's reported **utilization**, the bottom bar is how far the window
 * has **elapsed** toward its own reset. Both are percentages in `[0, 100]`, clamped here
 * rather than trusted pre-clamped from the caller.
 *
 * The reading: **top bar ahead of bottom bar = on pace to exhaust this window before it
 * resets.** Top behind bottom is the comfortable case — usage is lagging the window's
 * own clock. The utilization also reads as a whole-percent figure closing the row.
 *
 * The `blocks` variant is the compact form of the same reading: {@link PACE_BLOCKS} blocks filled to
 * the utilization, a notch where the window's clock stands, the whole line red once usage runs ahead of
 * that clock, and the figures as its tooltip.
 */
@Component({
  selector: 'fleet-kit-pace-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-pace-bar.html',
  styleUrl: './kit-pace-bar.css',
  host: { '[class.blocks]': 'blocks()', '[class.ahead]': 'blocks() && ahead()' },
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

  /** How the window is drawn — see {@link PaceBarVariant}. */
  readonly variant = input<PaceBarVariant>('bars');

  /** What the `blocks` variant's accessible name leads with, ahead of the window's figures — the subscription it belongs to. */
  readonly subject = input<string | null>(null);

  protected readonly clampedUtilization = computed(() => clampPct(this.utilizationPct()));
  protected readonly clampedElapsed = computed(() => clampPct(this.elapsedPct()));
  protected readonly utilizationLabel = computed(() => `${Math.round(this.clampedUtilization())}%`);

  protected readonly blocks = computed(() => this.variant() === 'blocks');
  protected readonly denseAfter = DENSE_AFTER;

  /** Usage is past the window's clock — on pace to exhaust the window before it resets. */
  protected readonly ahead = computed(() => this.clampedUtilization() > this.clampedElapsed());

  /** The figures, for the `blocks` variant's tooltip: "5h · 62% used · 70% of window". */
  protected readonly hint = computed(
    () => `${this.label()} · ${Math.round(this.clampedUtilization())}% used · ${Math.round(this.clampedElapsed())}% of window`,
  );

  protected readonly ariaLabel = computed(() => (this.subject() === null ? this.hint() : `${this.subject()} ${this.hint()}`));

  protected readonly cells = computed<readonly PaceBlock[]>(() => {
    const used = toTenths(this.clampedUtilization());
    const clock = toTenths(this.clampedElapsed());
    return Array.from({ length: PACE_BLOCKS }, (_unused, i) => ({
      on: i < used,
      clockBefore: i === clock,
      clockAfter: i === PACE_BLOCKS - 1 && clock === PACE_BLOCKS,
    }));
  });
}
