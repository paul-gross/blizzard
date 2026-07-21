import { ENVIRONMENT_INITIALIZER, type EnvironmentProviders, inject, makeEnvironmentProviders } from '@angular/core';
import { Router } from '@angular/router';

import { client as hubClient } from '../api/hub/client.gen';
import { redirectToLogin } from './auth-redirect';

/**
 * The 401 interceptor (issue #93): registers a response interceptor on the generated
 * hub client's own transport — the app has no `HttpClient`/`HttpInterceptorFn` seam to
 * hang off (`bzh:generated-client`'s fetch-based client is the one transport every
 * request rides), so this is that seam's counterpart. Any hub response answering
 * `401` (an unresolved or expired session — the one status `require()`/`/api/me`
 * raise for "not authenticated", never for "authenticated but lacking a permission",
 * which is `403` and left alone here) routes the app to `/login`, stashing the
 * current route for a post-login return (`redirectToLogin`).
 *
 * Registered once, app-wide (`provideAuthInterceptor()` in `app.config.ts`) via an
 * `ENVIRONMENT_INITIALIZER` — the standard Angular seam for a factory that needs
 * `inject()` (here, `Router`) but produces no injectable of its own, only a
 * side-effecting registration run once at bootstrap.
 */
export function provideAuthInterceptor(): EnvironmentProviders {
  return makeEnvironmentProviders([
    {
      provide: ENVIRONMENT_INITIALIZER,
      multi: true,
      useFactory: () => {
        const router = inject(Router);
        return () => {
          hubClient.interceptors.response.use((response) => {
            if (response.status === 401) {
              redirectToLogin(router);
            }
            return response;
          });
        };
      },
    },
  ]);
}
