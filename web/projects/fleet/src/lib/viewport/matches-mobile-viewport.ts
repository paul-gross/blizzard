import { inject } from '@angular/core';
import type { CanMatchFn } from '@angular/router';

import { ViewportService } from './viewport-service';

/**
 * The route-table fork (design review: the mobile/desktop switch belongs in the
 * route table, not a per-page `@if`, so a page component stays single-shell and
 * the switch exists exactly once). A route guarded with this `CanMatchFn` only
 * matches while {@link ViewportService.mode} reads `'mobile'` — pair it with an
 * unguarded fallback route of the same path for the desktop shell, so the URL
 * itself never forks (a deep link works in both modes).
 *
 * `canMatch` re-runs whenever the router (re)evaluates the route config for a
 * navigation — `provideViewportRenavigation` in `./viewport-renavigation.ts` is
 * what triggers that re-evaluation on a live mode flip, since the router does
 * not otherwise know `mode()` changed underneath an already-resolved route.
 */
export const matchesMobileViewport: CanMatchFn = () => inject(ViewportService).mode() === 'mobile';
