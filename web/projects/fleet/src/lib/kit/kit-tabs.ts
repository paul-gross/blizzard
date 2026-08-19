import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { KitTab, KitTabStrip } from './kit-tab';

/** One tab in a {@link KitTabs} strip. */
export interface KitTabOption {
  readonly value: string;
  readonly label: string;
  /** Optional per-tab test hook, forwarded to the rendered tab button. */
  readonly testid?: string;
}

/**
 * The tab strip (issue #160) — a row of tabs selecting between sibling views
 * of the same page (e.g. a chunk detail page's General/Artifacts split).
 * Shaped like {@link KitChips} (options + selected value in, `(choose)` out),
 * rendered with the same {@link KitTabStrip}/{@link KitTab} chrome the main
 * nav's routed tabs wear (blizzard#203) — one tab treatment, not two.
 *
 * `role="tablist"` / `role="tab"` / `aria-selected` so the strip is
 * navigable by assistive tech; this component does not itself own the
 * panel the tabs switch between — a consumer's own `@switch` does that.
 */
@Component({
  selector: 'fleet-kit-tabs',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitTab, KitTabStrip],
  templateUrl: './kit-tabs.html',
  styleUrl: './kit-tabs.css',
})
export class KitTabs {
  readonly options = input.required<readonly KitTabOption[]>();
  readonly activeValue = input<string | null>(null);

  /** Emits the clicked option's `value`. */
  readonly choose = output<string>();
}
