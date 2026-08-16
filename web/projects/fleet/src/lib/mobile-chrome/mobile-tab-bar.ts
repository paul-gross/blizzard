import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

/**
 * One tab the {@link MobileTabBar} renders — the minimal shape both the
 * hub's Board/Asks/Fleet strip and the runner's Board/Asks/Transcripts/Events
 * strip actually need. A tab is either **routed** (`route` set — every tab
 * with a screen of its own today, rendered as an `<a routerLink>` with
 * `routerLinkActive` deriving the highlighted state) or **static** (`active`
 * set instead — for a shell with no router of its own, rendered as a
 * `<button>` whose highlighted state the caller asserts directly). `inert`
 * dims and disables a tab with no screen of its own yet (mock screen C's
 * footnote), the same `.tab.inert` treatment either element kind gets.
 */
export interface MobileTabItem {
  /** This tab's own `data-testid` — must be unique across every mount
   * (`bzh:frontend-kit`'s globally-unique handle rule); the badge's own
   * handle is named separately via {@link badgeTestid}, never derived. */
  readonly testid: string;
  readonly label: string;
  /** A trailing count badge; omitted (not rendered as `0`) when falsy. */
  readonly badge?: number | null;
  /** The badge span's `data-testid`, required whenever {@link badge} is used. */
  readonly badgeTestid?: string | null;
  /** A route this tab navigates to — renders as a router-active `<a>`. Mutually
   * exclusive with {@link active}; omit both for a plain, unhighlighted tab. */
  readonly route?: string | null;
  /** How this tab's link handles the current query params on navigation —
   * `''` (default) drops them, right for a shell with no cross-route
   * selection state. A consumer whose routes share one (the runner's
   * `?chunk=`, `panel-selection.ts`) sets `'preserve'` per tab
   * that needs it — each consumer's own route wiring stays theirs, never a
   * bar-wide default that would also reach an unrelated consumer's routes
   * (e.g. the hub's, whose chunk-detail selection params `/board`/`/events`
   * don't read). */
  readonly queryParamsHandling?: 'merge' | 'preserve' | '';
  /** Statically marks this tab highlighted — for a shell with no router of
   * its own, whose one always-current screen has no route to reflect. */
  readonly active?: boolean;
  /** Dims the tab and disables its click/nav — no screen of its own yet. */
  readonly inert?: boolean;
}

/**
 * The generic mobile bottom tab bar (mock screen C's `.tabbar`,
 * `../../../docs/designs/mobile/core-flows.html`) — shared by the hub's
 * app-root fork (`hub/src/app/nav/mobile-tab-bar.ts`) and the runner's own
 * (`runner/src/app/nav/mobile-tab-bar.ts`), both thin wrappers around this.
 * Presentational only: it renders exactly the {@link MobileTabItem} list it
 * is handed and injects no query of its own — each consumer's own badge
 * counts and route wiring stay theirs.
 */
@Component({
  selector: 'fleet-mobile-tab-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, RouterLinkActive],
  template: `
    <nav class="tabbar" [attr.data-testid]="testid()">
      @for (item of items(); track item.testid) {
        @if (item.route && !item.inert) {
          <a
            class="tab"
            [routerLink]="item.route"
            [queryParamsHandling]="item.queryParamsHandling ?? ''"
            routerLinkActive="on"
            [attr.data-testid]="item.testid"
          >
            {{ item.label }}
            @if (item.badge) {
              <span class="badge" [attr.data-testid]="item.badgeTestid ?? null">{{ item.badge }}</span>
            }
          </a>
        } @else {
          <button
            class="tab"
            [class.inert]="item.inert"
            [class.on]="item.active"
            type="button"
            [disabled]="!!item.inert"
            [attr.data-testid]="item.testid"
          >
            {{ item.label }}
            @if (item.badge) {
              <span class="badge" [attr.data-testid]="item.badgeTestid ?? null">{{ item.badge }}</span>
            }
          </button>
        }
      }
    </nav>
  `,
  styles: `
    :host {
      display: block;
      flex: none;
    }
    .tabbar {
      display: flex;
      border-top: 1px solid var(--bezel);
      background: linear-gradient(180deg, var(--header-hi), var(--header-lo));
    }
    .tab {
      flex: 1;
      text-align: center;
      padding: 10px 0 8px;
      color: var(--label-dim);
      font-family: var(--mono);
      font-size: var(--fs-label);
      text-transform: uppercase;
      letter-spacing: 0.12em;
      border: 0;
      background: none;
      position: relative;
      text-decoration: none;
    }
    .tab.on {
      color: var(--amber);
    }
    .tab.on::before {
      content: '';
      position: absolute;
      top: 0;
      left: 25%;
      right: 25%;
      height: 2px;
      background: var(--amber);
    }
    /* Present but not yet functional — dimmed further than an inactive tab so
       neither reads as a live, broken link. */
    .tab.inert {
      opacity: 0.55;
      cursor: default;
    }
    .badge {
      display: inline-block;
      min-width: 16px;
      padding: 0 4px;
      margin-left: 5px;
      background: var(--red);
      color: var(--snow);
      border-radius: 8px;
      font-size: 10px;
      line-height: 16px;
      letter-spacing: 0;
    }
  `,
})
export class MobileTabBar {
  /** The tab list, in render order. */
  readonly items = input.required<readonly MobileTabItem[]>();

  /** The `<nav>`'s own `data-testid`. Defaults to the hub's existing handle. */
  readonly testid = input('mobile-tab-bar');
}
