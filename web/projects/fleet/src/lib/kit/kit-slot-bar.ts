import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/** A slot bar cell: filled, empty, or filled past the bar's total. */
type SlotCell = 'on' | 'off' | 'over';

/**
 * The env-slot flexbar — a row of `total` equal-width cells with the
 * first `used` filled, plus a `used/total slots` label; a `used` past `total` draws its excess as
 * further cells in pulsing cyan, rendering a runner's
 * environment-pool occupancy in the fleet registry (the mockup's `.gauge`/`.cell.on`
 * treatment). Presentational, input-only: `used` and `total` are handed in; this owns only the cell layout and the tokens-only styling, so
 * every slot bar reads identically. Given a `name`, it draws one compact line instead — the name,
 * then smaller cells, the figures only in its tooltip. A consumer gauging something other than a
 * runner's own pool retints the filled cells through `--kit-slot-bar-on` and `--kit-slot-bar-on-border`.
 */
@Component({
  selector: 'fleet-kit-slot-bar',
  host: { '[class.named]': 'name() !== null' },
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-slot-bar.html',
  styleUrl: './kit-slot-bar.css',
})
export class KitSlotBar {
  /** Environments currently held — the count of filled cells (the slot bar's numerator). */
  readonly used = input.required<number>();

  /** The runner's configured environment-pool size — the total cell count (the denominator). */
  readonly total = input.required<number>();

  /** The noun the label counts, for a bar that gauges something other than slots. */
  readonly unit = input<string>('slots');

  /** What the bar gauges, named left of its cells; `null` draws the cells over their count. */
  readonly name = input<string | null>(null);

  /** The reading in figures — `used/total unit`. */
  protected readonly count = computed(() => `${this.used()}/${this.total()} ${this.unit()}`);

  /** One entry per cell: the first `used` filled, the rest of `total` empty — and an
   * over-count past `total` drawn as that many more cells, each `over`. A negative used fills none. */
  protected readonly cells = computed<SlotCell[]>(() => {
    const total = Math.max(0, this.total());
    const used = Math.max(0, this.used());
    return Array.from({ length: Math.max(total, used) }, (_unused, i) => (i >= total ? 'over' : i < used ? 'on' : 'off'));
  });
}
