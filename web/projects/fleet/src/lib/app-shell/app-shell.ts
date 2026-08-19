import { ChangeDetectionStrategy, Component } from '@angular/core';

/**
 * The shared app-root shell — the top-level ordering both the hub and the
 * runner app roots render their chrome through, rather than each hand-rolling
 * its own `.layout`/`.shell` flex column (issue #325). Four projection slots,
 * fixed in this DOM order: `[shell-header]` (the desktop app header or the
 * mobile titlebar), `[shell-nav]` (the desktop tab strip), the default slot
 * (the routed content, typically a `<router-outlet>`), and `[shell-tab-bar]`
 * (the mobile bottom bar). Because both apps compose the same component for
 * this, header-above-nav-above-content is enforced by construction — neither
 * app can independently drift into nav-above-header the way the runner once
 * did (its header used to live *inside* the routed layout, below the
 * `<router-outlet>` anchor, rather than at the app root beside it).
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
