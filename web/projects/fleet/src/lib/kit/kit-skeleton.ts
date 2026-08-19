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
  templateUrl: './kit-skeleton.html',
  styleUrl: './kit-skeleton.css',
})
export class KitSkeleton {
  /** How many placeholder bars to render. */
  readonly rows = input(3);

  /** `'line'` (default) for a text-row placeholder; `'card'` for a taller
   * block, sized for a board-style card. */
  readonly variant = input<'line' | 'card'>('line');

  protected readonly rowIndexes = computed(() => Array.from({ length: this.rows() }, (_, i) => i));
}
