import { inject, type Signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, NavigationEnd, Router } from '@angular/router';
import { filter, map } from 'rxjs';

/**
 * Reads of the router's own state that a list route needs and `ActivatedRoute`
 * does not hand it directly. Both are plain injection-context functions rather
 * than a service, so a component reaches them the way it reaches a query.
 */

/**
 * The value of `name` on this route's currently activated child, as a signal —
 * `null` whenever the active child declares no such param.
 *
 * A master/detail tab is one parent list route with two children (`app.routes.ts`):
 * a bare child that selects nothing and a `:param` child that names the selection.
 * The parent survives navigation between the two — that is what the nesting buys,
 * and why a filter the parent holds is no longer thrown away on every row click —
 * so the parent's own `paramMap` never carries the selection, and the list's
 * highlight has to read its child's instead.
 *
 * `ActivatedRoute.firstChild` resolves live against the router state tree, which
 * the router rebuilds in full before it creates or updates a single component. A
 * snapshot read on every `NavigationEnd`, plus one at construction for the
 * activation already in flight, is therefore always the current child's.
 *
 * Both halves of `firstChild?.snapshot` are guarded, and the second guard is the
 * load-bearing one. Every child route under a gardening tab is `loadComponent`
 * (`app.routes.ts`), and on a tab-to-tab navigation the incoming list page is
 * constructed while its own lazy child is still resolving: `firstChild` is already
 * in the tree, `snapshot` is not yet on it. Reading through an unguarded
 * `.snapshot` throws there, and because the throw happens in a field initializer
 * the page never constructs and the navigation dies with no visible effect — the
 * tab simply stops responding. A cold load into the same URL resolves the whole
 * tree before creating anything and never sees it, which is why only navigation
 * *between* tabs is affected. `null` until the `NavigationEnd` below re-reads is
 * correct regardless: no child has been activated yet.
 */
export function injectChildRouteParam(name: string): Signal<string | null> {
  const route = inject(ActivatedRoute);
  const router = inject(Router);
  const read = (): string | null => route.firstChild?.snapshot?.paramMap.get(name) ?? null;
  return toSignal(
    router.events.pipe(
      filter((event) => event instanceof NavigationEnd),
      map(() => read()),
    ),
    { initialValue: read() },
  );
}

/** The URL's query params, read as signals and written as one merged navigation. */
export interface QueryFilters {
  /** `name`'s current value, or `null` when the URL carries no such param. A
   * signal read: a `computed` over it re-derives when the URL changes. */
  read(name: string): string | null;
  /** Merges `values` into the URL's query params in a single navigation — a
   * `null` drops its own param, and every param not named is left alone. */
  patch(values: Record<string, string | null>): void;
}

/**
 * The URL as the one home for a surface's filter state.
 *
 * A filter held in a component signal dies with the component and is invisible to
 * anyone the operator sends the link to. Held here it survives every navigation
 * the tab makes, and a filtered view is shareable by URL — the same reason the
 * selection itself lives in the path rather than in a signal.
 *
 * `patch` navigates with no path commands, which leaves the URL's path exactly as
 * it is (the detail child's own segment included) and rewrites only the query
 * string, and `replaceUrl` so adjusting a filter never buries the previous page
 * under history entries the operator has to click back through.
 */
export function injectQueryFilters(): QueryFilters {
  const route = inject(ActivatedRoute);
  const router = inject(Router);
  const params = toSignal(route.queryParamMap, { initialValue: route.snapshot.queryParamMap });
  return {
    read: (name) => params().get(name),
    patch: (values) => {
      void router.navigate([], { queryParams: values, queryParamsHandling: 'merge', replaceUrl: true });
    },
  };
}
