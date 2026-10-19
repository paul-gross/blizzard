import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

import { KitTab, KitTabStrip } from 'fleet';

import { PanelFiltersBar } from './panel-filters-bar';

/**
 * The panel's top-level tab strip — `Board`, `Events`, and `Machine`, the same three on
 * every page and under every filter, one `routerLink` each, wearing the same
 * {@link KitTabStrip}/{@link KitTab} chrome the hub's own `app-nav.ts` wears — one tab
 * treatment, not two — and the panel's filters ({@link PanelFiltersBar}) at its end.
 * `queryParamsHandling="merge"` carries the filters' `?runner=`/`?project=` and the board's
 * `?chunk=` selection (the shared `injectChunkUrlSelection`) across every tab switch.
 */
@Component({
  selector: 'app-nav',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitTab, KitTabStrip, PanelFiltersBar, RouterLink, RouterLinkActive],
  templateUrl: './app-nav.html',
  styleUrl: './app-nav.css',
})
export class AppNav {}
