import { type EnvironmentProviders, effect, inject, makeEnvironmentProviders, provideEnvironmentInitializer } from '@angular/core';
import { Router } from '@angular/router';

import { ViewportService } from './viewport-service';

/**
 * Re-runs routing whenever {@link ViewportService.mode} flips — the router
 * only re-evaluates a route's `canMatch` (see `./matches-mobile-viewport.ts`)
 * during a navigation, so without this a live viewport-override flip (or a
 * breakpoint crossing) would leave an already-resolved route's shell stale
 * until the next manual navigation.
 *
 * Re-navigates to the router's own current URL, which — paired with
 * `withRouterConfig({ onSameUrlNavigation: 'reload' })` on the consuming app's
 * `provideRouter` (same-URL navigations are a no-op otherwise) — re-runs
 * `canMatch` against the new mode and swaps the matched route's shell in
 * place, with no URL change.
 *
 * The initial `mode()` value is skipped: it is what already picked the route
 * that matched on first load, so re-navigating to it again would be a no-op
 * navigation for no reason.
 */
export function provideViewportRenavigation(): EnvironmentProviders {
  return makeEnvironmentProviders([
    provideEnvironmentInitializer(() => {
      const viewport = inject(ViewportService);
      const router = inject(Router);
      let first = true;

      effect(() => {
        viewport.mode();
        if (first) {
          first = false;
          return;
        }
        void router.navigateByUrl(router.url);
      });
    }),
  ]);
}
