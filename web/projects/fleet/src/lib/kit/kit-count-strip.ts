import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

/** One cell of a {@link KitCountStrip}: a count over its caption. */
export interface KitCountCell {
  readonly key: string;
  readonly count: number | string;
  readonly caption: string;
  readonly testid: string;
  /** A selectable cell is a toggle button; a static one is a plain readout. */
  readonly selectable: boolean;
  /** The count's colour, or `null`/absent for the default. */
  readonly countColor?: string | null;
}

/**
 * A strip of equal-width count cells — a count over its caption, the selected cell
 * pressed in. A selectable cell is a native toggle button carrying `aria-pressed`;
 * a static cell is a plain readout with no tab stop or affordance. The strip only
 * reports which cell was clicked: the consumer owns what selection means.
 */
@Component({
  selector: 'fleet-kit-count-strip',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-count-strip.html',
  styleUrl: './kit-count-strip.css',
})
export class KitCountStrip {
  readonly cells = input.required<readonly KitCountCell[]>();
  /** The selected cell's key, or `null` for none. */
  readonly selected = input<string | null>(null);
  readonly testid = input<string | null>(null);
  readonly cellClick = output<string>();

  protected readonly columns = computed(() => `repeat(${this.cells().length}, 1fr)`);
}
