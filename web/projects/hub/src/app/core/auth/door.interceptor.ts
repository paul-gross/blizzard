import { ENVIRONMENT_INITIALIZER, type EnvironmentProviders, makeEnvironmentProviders } from '@angular/core';

import { hubClient } from 'fleet/shell';

/** The header the hub reads to record which door a configured write came through. */
export const DOOR_HEADER = 'X-Blizzard-Door';

/**
 * The door interceptor: stamps every hub request on the generated client's transport
 * with `X-Blizzard-Door: board`.
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
