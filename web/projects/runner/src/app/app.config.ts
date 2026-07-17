import { ApplicationConfig, provideBrowserGlobalErrorListeners, provideZonelessChangeDetection } from '@angular/core';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

// Zoneless from day one; TanStack Query for server reads. No zone.js.
export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideZonelessChangeDetection(),
    provideTanStackQuery(new QueryClient()),
  ],
};
