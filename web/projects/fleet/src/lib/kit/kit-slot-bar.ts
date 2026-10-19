import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
} from '@angular/core';

/** A slot bar cell's fill: filled, empty, or filled past the bar's total. */
type SlotCellState = 'on' | 'off' | 'over';

/** One drawn cell: its fill, and — for a bar of named slots — the slot's tooltip and whether it dims. */
interface SlotCell {
  readonly state: SlotCellState;
  readonly title: string | null;
  readonly dim: boolean;
}

/** One named slot of a positional bar: whether it is held, its tooltip, and whether it dims. */
export interface SlotBarSlot {
  readonly on: boolean;
  readonly title?: string | null;
  readonly dim?: boolean;
}

/** Past this many blocks in one row, a row of blocks packs them closer, so a long row keeps its blocks wide enough to
 * read — the slot bar's cells and a subscription's window blocks alike. */
export const DENSE_AFTER = 10;

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
  host: { '[class.named]': 'name() !== null', '[class.dense]': 'dense()' },
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

  /** The bar's slots in their own order, each lit where held — set, the bar draws one cell per slot in place of
   * filling `used` of `total` from the left, so which slot is held reads, not only how many. */
  readonly slots = input<readonly SlotBarSlot[] | null>(null);

  /** The reading in figures — `used/total unit`. */
  protected readonly count = computed(
    () => `${this.used()}/${this.total()} ${this.unit()}`,
  );

  /** One entry per cell: the first `used` filled, the rest of `total` empty — and an
   * over-count past `total` drawn as that many more cells, each `over`. A negative used fills none. */
  protected readonly cells = computed<SlotCell[]>(() => {
    const slots = this.slots();
    if (slots !== null) {
      return slots.map((slot) => ({
        state: slot.on ? 'on' : 'off',
        title: slot.title ?? null,
        dim: slot.dim ?? false,
      }));
    }
    const total = Math.max(0, this.total());
    const used = Math.max(0, this.used());
    return Array.from({ length: Math.max(total, used) }, (_unused, i) => ({
      state: i >= total ? 'over' : i < used ? 'on' : 'off',
      title: null,
      dim: false,
    }));
  });

  /** Whether the bar packs its cells closer — past {@link DENSE_AFTER} of them. */
  protected readonly dense = computed(() => this.cells().length > DENSE_AFTER);
}
