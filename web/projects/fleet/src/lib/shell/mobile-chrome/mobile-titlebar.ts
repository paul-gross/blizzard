import { ChangeDetectionStrategy, Component, TemplateRef, computed, input } from '@angular/core';

import { BrandMark } from '../../core/design';
import { KitMenu } from '../../kit';

/**
 * The generic mobile titlebar (mock screen C's `.appbar`,
 * `../../../docs/designs/mobile/core-flows.html`) — the brand mark + wordmark,
 * an optional caption beside them, a live dot, and a buried `⋮` overflow menu,
 * for a mobile shell.
 * Presentational only — the `live` boolean and the menu's panel are the
 * consumer's own concern; this component owns only the titlebar chrome.
 *
 * The menu's contents arrive as a {@link TemplateRef} rather than as projected
 * content, per {@link KitMenu}'s contract.
 *
 * `testid` roots the handles this component renders itself (`${testid}`,
 * `${testid}-livedot`, `${testid}-menu`) (`bzh:frontend-kit`'s globally-unique
 * handle rule), plus the caption (`${testid}-caption`) and detail (`${testid}-detail`).
 * The panel's own handle belongs to whoever declares the panel.
 */
@Component({
  selector: 'fleet-mobile-titlebar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BrandMark, KitMenu],
  templateUrl: './mobile-titlebar.html',
  styleUrl: './mobile-titlebar.css',
})
export class MobileTitlebar {
  /** Whether the consumer's own live signal is currently connected — the hub's
   * `FleetLiveUpdates` stream state, the runner's hub-reachability read. */
  readonly live = input.required<boolean>();

  /** The overflow menu's panel — the `<ng-template>` holding a
   * `fleet-kit-menu-panel` and its items, declared in the consumer's own view. */
  readonly menu = input.required<TemplateRef<unknown>>();

  /** The root header's `data-testid`; every other handle this component
   * renders derives from it. Defaults to the hub's existing `mobile-titlebar`. */
  readonly testid = input('mobile-titlebar');

  /** A short line beside the wordmark — the hub names the tenant the board is in — truncated to the room the titlebar
   * leaves it, its full text on hover. Empty renders nothing. */
  readonly caption = input('');

  /** A second short line after the caption — the hub names the project its lens is on — truncated like the caption,
   * its full text on hover. Empty renders nothing. */
  readonly detail = input('');

  protected readonly captionTestid = computed(() => `${this.testid()}-caption`);
  protected readonly detailTestid = computed(() => `${this.testid()}-detail`);

  protected readonly livedotTestid = computed(() => `${this.testid()}-livedot`);
  protected readonly menuTestid = computed(() => `${this.testid()}-menu`);
}
