import { ChangeDetectionStrategy, Component, TemplateRef, computed, input } from '@angular/core';

import { BrandMark } from '../../core/design';
import { KitMenu } from '../../kit';

/**
 * The generic mobile titlebar (mock screen C's `.appbar`,
 * `../../../docs/designs/mobile/core-flows.html`) — the brand mark + wordmark,
 * a live dot, and a buried `⋮` overflow menu, for a mobile shell.
 * Presentational only — the `live` boolean and the menu's panel are the
 * consumer's own concern; this component owns only the titlebar chrome.
 *
 * The menu's contents arrive as a {@link TemplateRef} rather than as projected
 * content, matching {@link KitMenu}'s contract: a `CdkMenu` panel wrapped around an `<ng-content>` slot cannot
 * see the items projected into it, so the panel is declared in the consumer's
 * own view and passed here.
 *
 * `testid` roots the handles this component renders itself (`${testid}`,
 * `${testid}-livedot`, `${testid}-menu`), so two mounts (hub and runner) never
 * collide on the same `data-testid` (`bzh:frontend-kit`'s globally-unique
 * handle rule). The panel's own handle belongs to whoever declares the panel.
 * Defaults to `'mobile-titlebar'` — the hub's existing handle — so the hub side
 * needs no input to keep its specs passing.
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

  protected readonly livedotTestid = computed(() => `${this.testid()}-livedot`);
  protected readonly menuTestid = computed(() => `${this.testid()}-menu`);
}
