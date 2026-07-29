import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { formatAbsolute, formatWhen } from '../when';

/**
 * The board's short-form timestamp with its full local date + time as a hover
 * tooltip (issue #175) — bundles {@link formatWhen}'s short text and
 * {@link formatAbsolute}'s title in one node, so a template-render call site reaches
 * both with one binding instead of hand-wiring a `title` beside it. The view-model
 * and split-render call sites {@link formatAbsolute} also serves render its title
 * directly instead, since there is no single short-form node here to wrap.
 *
 * Renders as its own host element (`<fleet-when>`), so a caller's `class`/`data-*`
 * attributes land on it exactly as they would on the `<span>` it replaces.
 */
@Component({
  selector: 'fleet-when',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `{{ text() }}`,
  host: {
    '[attr.title]': 'title()',
  },
})
export class FleetWhen {
  /** The ISO instant to render. */
  readonly iso = input.required<string>();

  protected readonly text = computed(() => formatWhen(this.iso()));
  protected readonly title = computed(() => formatAbsolute(this.iso()) || null);
}
