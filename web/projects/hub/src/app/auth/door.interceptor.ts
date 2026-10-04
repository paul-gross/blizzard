import { ENVIRONMENT_INITIALIZER, type EnvironmentProviders, makeEnvironmentProviders } from '@angular/core';

import { hubClient } from 'fleet/shell';

/** The header the hub reads to record which door a configured write came through. */
export const DOOR_HEADER = 'X-Blizzard-Door';

/**
 * The door interceptor: stamps every hub request with `X-Blizzard-Door: board`, so a
 * configured-record write made from the board is recorded in the change log as the board's
 * rather than as a bare API call. Registered once, app-wide (`provideDoorInterceptor()` in
 * `app.config.ts`), beside the 401 interceptor and on the same generated client transport.
 */
export function provideDoorInterceptor(): EnvironmentProviders {
  return makeEnvironmentProviders([
    {
      provide: ENVIRONMENT_INITIALIZER,
      multi: true,
      useValue: () => {
        hubClient.interceptors.request.use((request) => {
          request.headers.set(DOOR_HEADER, 'board');
          return request;
        });
      },
    },
  ]);
}
