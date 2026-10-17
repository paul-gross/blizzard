import { ENVIRONMENT_INITIALIZER, type EnvironmentProviders, inject, makeEnvironmentProviders } from '@angular/core';
import { Router } from '@angular/router';

import { hubClient } from 'fleet/shell';
import { redirectToLogin } from './auth-redirect';

/**
 * The 401 interceptor: registers a response interceptor on the generated
 * hub client's transport. Any hub response answering `401` (a `403` is left alone)
 * routes the app to `/login`, stashing the current route for a post-login return
 * (`redirectToLogin`).
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
