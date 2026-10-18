import { ApplicationConfig, provideBrowserGlobalErrorListeners, provideZonelessChangeDetection } from '@angular/core';
import { provideRouter, withRouterConfig } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { provideAuthInterceptor } from '../core/auth/auth.interceptor';
import { provideDoorInterceptor } from '../core/auth/door.interceptor';
import { provideTenantInterceptor } from '../core/auth/tenant.interceptor';
import { provideFlags, provideViewportRenavigation } from 'fleet/shell';
import { hubFlagsRead } from '../core/flags.query';
import { provideProjectLens } from '../core/project-lens';

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
    provideAuthInterceptor(),
    provideDoorInterceptor(),
    // Every hub call — the generated client's and the live stream's — names the board's tenant.
    provideTenantInterceptor(),
    // The project lens narrows the reads and keeps the links under it, both read off the URL.
    provideProjectLens(),
    // Features read the hub's flags through the shared store: one read at start.
    provideFlags(hubFlagsRead),
  ],
};
