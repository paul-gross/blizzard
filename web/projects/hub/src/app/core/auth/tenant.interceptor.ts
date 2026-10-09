import {
  DestroyRef,
  ENVIRONMENT_INITIALIZER,
  type EnvironmentProviders,
  inject,
  makeEnvironmentProviders,
} from '@angular/core';

import { EVENT_SOURCE_FACTORY, type EventSourceFactory, FetchEventSource, hubClient } from 'fleet/shell';

import { TENANT_HEADER, TenantContext } from './tenant-context';

/** The tenant header for the board's current tenant, empty while none is known. */
function tenantHeaders(context: TenantContext): Record<string, string> {
  const tenant = context.current();
  return tenant === null ? {} : { [TENANT_HEADER]: tenant };
}

/**
 * The tenant interceptor: every hub call the board makes names {@link TenantContext}'s tenant in
 * `X-Blizzard-Tenant`. The board reaches the hub two ways, and both read the one context — the generated client,
 * through a request interceptor on its transport, and the live event stream, a fetch of its own outside that client,
 * through the {@link EVENT_SOURCE_FACTORY} it is opened with. Registered once, app-wide, in `app.config.ts`.
 */
export function provideTenantInterceptor(): EnvironmentProviders {
  return makeEnvironmentProviders([
    {
      provide: ENVIRONMENT_INITIALIZER,
      multi: true,
      useValue: () => {
        const context = inject(TenantContext);
        const registration = hubClient.interceptors.request.use((request) => {
          for (const [name, value] of Object.entries(tenantHeaders(context))) request.headers.set(name, value);
          return request;
        });
        // The generated client outlives this injector; its interceptor must not keep naming this context's tenant.
        inject(DestroyRef).onDestroy(() => hubClient.interceptors.request.eject(registration));
      },
    },
    {
      provide: EVENT_SOURCE_FACTORY,
      useFactory: (): EventSourceFactory => {
        const context = inject(TenantContext);
        return (url) => new FetchEventSource(url, tenantHeaders(context));
      },
    },
  ]);
}
