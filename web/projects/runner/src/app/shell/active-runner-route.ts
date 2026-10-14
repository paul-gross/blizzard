import type { ActivatedRouteSnapshot } from '@angular/router';

/** The `:runnerId` a router state names, from its root down, or `null` on a host page. */
export function routeRunnerId(root: ActivatedRouteSnapshot): string | null {
  for (let route: ActivatedRouteSnapshot | null = root; route !== null; route = route.firstChild) {
    const id = route.paramMap.get('runnerId');
    if (id !== null) return id;
  }
  return null;
}
