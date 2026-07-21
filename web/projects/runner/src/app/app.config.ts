import { ApplicationConfig, provideBrowserGlobalErrorListeners, provideZonelessChangeDetection } from '@angular/core';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { routes } from './app.routes';

// Zoneless from day one; TanStack Query for server reads. No zone.js.
export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideZonelessChangeDetection(),
    provideTanStackQuery(new QueryClient()),
    // Panel selection (which chunk is open, which attempt tab is active) lives in
    // the URL's query params so it is shareable and refresh-safe (issue #99);
    // `LocalPanel` reads and writes them through the router. See `app.routes.ts`
    // for why a single catch-all route is all the table needs.
    provideRouter(routes),
  ],
};
