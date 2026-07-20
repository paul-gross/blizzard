import { ApplicationConfig, provideBrowserGlobalErrorListeners, provideZonelessChangeDetection } from '@angular/core';
import { provideRouter, withRouterConfig } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { provideViewportRenavigation } from 'fleet';

import { routes } from './app.routes';

// Zoneless from day one. Server reads go through TanStack Query
// (request/response cache + invalidation); no zone.js. The QueryClient is a
// single app-scoped instance for the app's life — it must never be recreated
// by a routed component, or the query cache would drop on every tab switch.
export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideZonelessChangeDetection(),
    provideTanStackQuery(new QueryClient()),
    // `onSameUrlNavigation: 'reload'` is required by `provideViewportRenavigation`
    // below — it re-navigates to the router's own current URL on a viewport-mode
    // flip so `board`'s route-table guard (`matchesMobileViewport`) re-evaluates;
    // without this config, a same-URL navigation is a router no-op.
    provideRouter(routes, withRouterConfig({ onSameUrlNavigation: 'reload' })),
    provideViewportRenavigation(),
  ],
};
