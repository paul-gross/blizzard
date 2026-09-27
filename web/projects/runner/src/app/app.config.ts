import { ApplicationConfig, provideBrowserGlobalErrorListeners, provideZonelessChangeDetection } from '@angular/core';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { provideSessionRecovery } from 'local-panel';

import { routes } from './app.routes';

// Zoneless from day one; TanStack Query for server reads. No zone.js.
export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideZonelessChangeDetection(),
    provideTanStackQuery(new QueryClient()),
    // Panel selection (which chunk is open) lives in the URL's `?chunk=` query
    // param so it is shareable and refresh-safe; `LocalPanel` reads
    // and writes it through the router. See `app.routes.ts` for the route table
    // this now resolves against.
    provideRouter(routes),
    // Session reacquisition on a 401 — the runner client's own
    // interceptor, mirroring the hub app's `provideAuthInterceptor()`.
    provideSessionRecovery(),
  ],
};
