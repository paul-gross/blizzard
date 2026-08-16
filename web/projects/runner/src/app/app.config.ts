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
    // param so it is shareable and refresh-safe (issue #99); `LocalPanel` reads
    // and writes it through the router. See `app.routes.ts` for the route table
    // this now resolves against (issue #313).
    provideRouter(routes),
    // Session reacquisition on a 401 (issue #312) — the runner client's own
    // interceptor, mirroring the hub app's `provideAuthInterceptor()`.
    provideSessionRecovery(),
  ],
};
