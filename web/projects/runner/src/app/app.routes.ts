import type { Routes } from '@angular/router';

/**
 * The runner app's route table. The machine panel is a single screen with no
 * sub-pages, so there is nothing to route *between* — selection (which chunk is
 * open, which attempt tab is active) rides in the URL's query params instead
 * (issue #99), read and written by `LocalPanel`. The path itself never changes:
 * it is always `''`, gaining a `?chunk=…&attempt=…` string as selections are
 * made, so a reload never needs server-side route rewriting.
 *
 * This one catch-all route exists only so the router has *something* to match on
 * that (always-`''`) path — without it the initial navigation logs a
 * no-route-matched error. It renders nothing: the panel is composed directly by
 * `App`, not through a `<router-outlet>`. The router is wired purely to give
 * `LocalPanel` the query-param binding.
 */
export const routes: Routes = [{ path: '**', children: [] }];
