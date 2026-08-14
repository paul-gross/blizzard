import { ENVIRONMENT_INITIALIZER, type EnvironmentProviders, inject, makeEnvironmentProviders } from '@angular/core';
import { runnerClient } from 'fleet';

import { SessionRecovery } from './session-recovery';

/**
 * Registers {@link SessionRecovery}'s response interceptor on the generated
 * runner client's own transport (issue #312) — the runner client is a separate
 * transport from the hub's, with no interceptor of its own, so the runner app's
 * `app.config.ts` provides this the way the hub app provides
 * `provideAuthInterceptor()`. An `ENVIRONMENT_INITIALIZER` (not a plain factory)
 * because registration needs `inject()` (here, {@link SessionRecovery}) but
 * produces no injectable of its own — only a side-effecting registration run
 * once at bootstrap.
 */
export function provideSessionRecovery(): EnvironmentProviders {
  return makeEnvironmentProviders([
    {
      provide: ENVIRONMENT_INITIALIZER,
      multi: true,
      useFactory: () => {
        const recovery = inject(SessionRecovery);
        return () => {
          runnerClient.interceptors.response.use((response, request) => recovery.handle(response, request));
        };
      },
    },
  ]);
}
