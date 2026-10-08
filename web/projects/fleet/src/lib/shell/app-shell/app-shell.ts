import { ChangeDetectionStrategy, Component } from '@angular/core';

/**
 * The shared app-root shell — the top-level ordering both the hub and the
 * runner app roots render their chrome through, rather than each hand-rolling
 * its own `.layout`/`.shell` flex column. Four projection slots,
 * fixed in this DOM order: `[shell-header]` (the desktop app header or the
 * mobile titlebar), `[shell-nav]` (the desktop tab strip), the default slot
 * (the routed content, typically a `<router-outlet>`), and `[shell-tab-bar]`
 * (the mobile bottom bar). Because both apps compose the same component for
 * this, header-above-nav-above-content is enforced by construction — neither
 * app can independently drift into nav-above-header, as it would if a header
 * lived *inside* the routed layout, below the `<router-outlet>` anchor.
 *
 * Presentational only, no inputs: it owns just the DOM order and the
 * flex/height/overflow chrome a full-height, non-scrolling app shell needs.
 * Content projected into any slot keeps the *declaring* component's own style
 * scope (Angular view encapsulation), not this one's — so each app still
 * declares its own `router-outlet { display: none }` rule (the anchor
 * element the router inserts routed components after, with no visual size of
 * its own) rather than this component trying to own a selector it cannot see
 * past the projection boundary.
 */
@Component({
  selector: 'fleet-app-shell',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './app-shell.html',
  styleUrl: './app-shell.css',
})
export class AppShell {}
