import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/**
 * A loading placeholder — a stack of shimmering bars (`'line'`) or blocks
 * (`'card'`), for a container that would rather show the shape of what is
 * coming than a bare status line (`KitAsyncState`'s `loadingMode="content"`
 * slot). Presentational: it takes only a count and a variant, no query.
 *
 * The shimmer honors `prefers-reduced-motion` (`kit-async-state.ts`'s own
 * `.dot.offline` blink follows the same rule) — a static bar reads as a
 * placeholder just as well as an animated one, and a user who has asked the
 * platform for less motion gets it here too.
 */
@Component({
  selector: 'fleet-kit-skeleton',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @for (i of rowIndexes(); track i) {
      <div class="bar" [class.card]="variant() === 'card'"></div>
    }
  `,
  styles: `
    :host {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .bar {
      height: 14px;
      background: linear-gradient(90deg, var(--panel-deep) 25%, var(--overlay-25) 37%, var(--panel-deep) 63%);
      background-size: 400% 100%;
      animation: fleet-kit-skeleton-shimmer 1.4s ease infinite;
    }
    .bar.card {
      height: 56px;
    }
    @keyframes fleet-kit-skeleton-shimmer {
      0% {
        background-position: 100% 50%;
      }
      100% {
        background-position: 0 50%;
      }
    }
    @media (prefers-reduced-motion: reduce) {
      .bar {
        animation: none;
      }
    }
  `,
})
export class KitSkeleton {
  /** How many placeholder bars to render. */
  readonly rows = input(3);

  /** `'line'` (default) for a text-row placeholder; `'card'` for a taller
   * block, sized for a board-style card. */
  readonly variant = input<'line' | 'card'>('line');

  protected readonly rowIndexes = computed(() => Array.from({ length: this.rows() }, (_, i) => i));
}
