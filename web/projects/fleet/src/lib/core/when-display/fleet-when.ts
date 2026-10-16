import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { injectNowSignal } from '../now-signal';
import { formatAbsolute, formatRelative, formatWhen } from '../when';

/**
 * The board's short-form timestamp with its full local date + time as a hover
 * tooltip — bundles {@link formatWhen}'s short text and
 * {@link formatAbsolute}'s title in one node, so a template-render call site reaches
 * both with one binding instead of hand-wiring a `title` beside it. The view-model
 * and split-render call sites {@link formatAbsolute} also serves render its title
 * directly instead, since there is no single short-form node here to wrap.
 *
 * Renders as its own host element (`<fleet-when>`), so a caller's `class`/`data-*`
 * attributes land on it exactly as they would on the `<span>` it replaces.
 *
 * `relative` swaps the short text for {@link formatRelative}'s `3 days ago` /
 * `in 2 hours` form; the tooltip stays absolute either way.
 *
 * `text` reads {@link injectNowSignal} (the shared one-second clock; the string
 * equality of the `computed()` gates re-render to when the text changes) rather than a reading taken inside `formatWhen`: for a static
 * `iso` (a completed chunk's `completedAt`, an immutable event's `recorded_at`),
 * `iso` is the only otherwise-tracked signal, so `computed()` would never
 * re-invoke past first render and a long-lived card would read a stale "23:45"
 * long after crossing into "Yesterday 23:45". `title` needs no such tick — its
 * text never changes for a fixed `iso`.
 */
@Component({
  selector: 'fleet-when',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './fleet-when.html',
  host: {
    '[attr.title]': 'title()',
  },
})
export class FleetWhen {
  /** The ISO instant to render. */
  readonly iso = input.required<string>();
  /** Render the distance from now instead of the board's short stamp. */
  readonly relative = input(false);

  private readonly now = injectNowSignal();

  protected readonly text = computed(() => {
    const now = new Date(this.now());
    return this.relative() ? formatRelative(this.iso(), now) : formatWhen(this.iso(), now);
  });
  protected readonly title = computed(() => formatAbsolute(this.iso()) || null);
}
