import { ApplicationConfig, provideBrowserGlobalErrorListeners, provideZonelessChangeDetection } from '@angular/core';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

// Zoneless from day one. Server reads go through TanStack Query
// (request/response cache + invalidation); no zone.js.
export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideZonelessChangeDetection(),
    provideTanStackQuery(new QueryClient()),
  ],
};
