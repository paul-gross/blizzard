import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { BrandMark } from '../design';
import { KitMenu } from '../kit';

/**
 * The generic mobile titlebar (mock screen C's `.appbar`,
 * `../../../docs/designs/mobile/core-flows.html`) — the brand mark + wordmark,
 * a live dot, and a buried `⋮` overflow menu, shared by both mobile shells:
 * the hub's app-root fork (`hub/src/app/nav/mobile-titlebar.ts`, a thin
 * wrapper around this) and the runner's `local-panel-mobile.ts`. Presentational
 * only — the `live` boolean and the menu's projected content are each
 * consumer's own concern (the hub derives it from `FleetLiveUpdates`, the
 * runner from its own hub-reachability read); this component owns only the
 * chrome both shells duplicated.
 *
 * `testid` roots every handle this component renders (`${testid}`,
 * `${testid}-livedot`, and — via {@link KitMenu}'s own `${testid}-panel`
 * derivation — `${testid}-menu`/`${testid}-menu-panel`), so two mounts (hub
 * and runner) never collide on the same `data-testid` (`bzh:frontend-kit`'s
 * globally-unique handle rule) without each consumer having to spell out
 * every derived name itself. Defaults to `'mobile-titlebar'` — the hub's
 * existing handle — so the hub side needs no input to keep its specs passing.
 */
@Component({
  selector: 'fleet-mobile-titlebar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BrandMark, KitMenu],
  template: `
    <header class="mt-appbar" [attr.data-testid]="testid()">
      <fleet-brand-mark [size]="22" />
      <span class="word">blizzard</span>
      <span class="spacer"></span>
      <span
        class="livedot"
        [class.active]="live()"
        [attr.title]="live() ? 'live' : 'offline'"
        [attr.data-testid]="livedotTestid()"
      ></span>
      <fleet-kit-menu class="menu" ariaLabel="Shell options" [testid]="menuTestid()">
        <ng-content />
      </fleet-kit-menu>
    </header>
  `,
  styles: `
    :host {
      display: block;
      flex: none;
    }
    .mt-appbar {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 16px;
      background: linear-gradient(180deg, var(--header-hi), var(--header-lo));
      border-bottom: 1px solid var(--bezel);
      font-family: var(--mono);
    }
    .word {
      color: var(--snow);
      font-size: var(--fs-md);
      font-weight: 700;
      letter-spacing: 0.06em;
    }
    .spacer {
      flex: 1;
    }
    .livedot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--label-dim);
    }
    .livedot.active {
      background: var(--cyan);
      box-shadow: 0 0 8px var(--cyan);
    }
    @media (prefers-reduced-motion: no-preference) {
      .livedot.active {
        animation: mobile-titlebar-pulse 2.2s ease-in-out infinite;
      }
    }
    @keyframes mobile-titlebar-pulse {
      50% {
        opacity: 0.35;
      }
    }
    .menu {
      color: var(--label);
    }
  `,
})
export class MobileTitlebar {
  /** Whether the consumer's own live signal is currently connected — the hub's
   * `FleetLiveUpdates` stream state, the runner's hub-reachability read. */
  readonly live = input.required<boolean>();

  /** The root header's `data-testid`; every other handle this component
   * renders derives from it. Defaults to the hub's existing `mobile-titlebar`. */
  readonly testid = input('mobile-titlebar');

  protected readonly livedotTestid = computed(() => `${this.testid()}-livedot`);
  protected readonly menuTestid = computed(() => `${this.testid()}-menu`);
}
