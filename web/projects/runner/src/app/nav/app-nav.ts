import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

import { KitTab, KitTabStrip } from 'fleet';

/**
 * The runner's top-level tab strip (issue #313) — `Board` and `Events`, one
 * `routerLink` each, wearing the same {@link KitTabStrip}/{@link KitTab}
 * chrome the hub's own `app-nav.ts` wears (blizzard#203) — one tab
 * treatment, not two. `queryParamsHandling="preserve"` carries the board's
 * `?chunk=` selection (`panel-selection.ts`, issue #99) across a
 * Board → Events → Board round trip rather than dropping it on navigation.
 */
@Component({
  selector: 'app-nav',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitTab, KitTabStrip, RouterLink, RouterLinkActive],
  templateUrl: './app-nav.html',
  styleUrl: './app-nav.css',
})
export class AppNav {}
