import { ApplicationConfig, provideBrowserGlobalErrorListeners, provideZonelessChangeDetection } from '@angular/core';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

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
    provideRouter(routes),
  ],
};
