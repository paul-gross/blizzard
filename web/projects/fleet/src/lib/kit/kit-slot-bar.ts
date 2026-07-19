import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/**
 * The env-slot flexbar (issue #69) — a row of `total` equal-width cells with the
 * first `used` filled, plus a `used/total slots` label, rendering a runner's
 * environment-pool occupancy in the fleet registry (the mockup's `.gauge`/`.cell.on`
 * treatment). Presentational, input-only: the container computes `used` (summed from
 * its chunks' `environment_count`) and `total` (the runner's reported `env_capacity`)
 * and hands them in; this owns only the cell layout and the tokens-only styling, so
 * every slot bar reads identically.
 */
@Component({
  selector: 'fleet-kit-slot-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="gauge" data-testid="slot-bar-gauge">
      @for (filled of cells(); track $index) {
        <span class="cell" [class.on]="filled"></span>
      }
    </div>
    <div class="gauge-lbl">
      <span class="lbl" data-testid="slot-bar-label">{{ used() }}/{{ total() }} slots</span>
    </div>
  `,
  styles: `
    :host {
      display: block;
    }
    .gauge {
      display: flex;
      gap: 3px;
      margin-top: 5px;
    }
    .cell {
      flex: 1;
      height: 10px;
      border: 1px solid var(--bezel);
      background: var(--panel-deep);
    }
    .cell.on {
      background: var(--amber);
      border-color: var(--amber-dim);
    }
    .gauge-lbl {
      display: flex;
      justify-content: flex-end;
      margin-top: 3px;
    }
    .lbl {
      font-size: var(--fs-label);
      color: var(--label-dim);
    }
  `,
})
export class KitSlotBar {
  /** Environments currently held — the count of filled cells (the slot bar's numerator). */
  readonly used = input.required<number>();

  /** The runner's configured environment-pool size — the total cell count (the denominator). */
  readonly total = input.required<number>();

  /** One entry per cell, `true` for the first `used` — clamped so an over-count never
   * renders more filled cells than exist, nor a negative used any at all. */
  protected readonly cells = computed<boolean[]>(() => {
    const total = Math.max(0, this.total());
    const used = Math.min(Math.max(0, this.used()), total);
    return Array.from({ length: total }, (_unused, i) => i < used);
  });
}
