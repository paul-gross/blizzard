import {
  DestroyRef,
  ENVIRONMENT_INITIALIZER,
  type EnvironmentProviders,
  inject,
  makeEnvironmentProviders,
} from '@angular/core';

import { EVENT_SOURCE_FACTORY, type EventSourceFactory, FetchEventSource, hubClient } from 'fleet/shell';

import { TENANT_HEADER, TenantContext } from './tenant-context';
import { TenantLoss } from './tenant-loss';

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
 *
 * Both ways also report a 404 to a call that named a tenant to {@link TenantLoss}, which settles from `/api/me` whether
 * the board has lost that tenant.
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
        const loss = inject(TenantLoss);
        const responses = hubClient.interceptors.response.use((response, request) => {
          const tenant = request.headers.get(TENANT_HEADER);
          if (response.status === 404 && tenant !== null) loss.notFound(tenant);
          return response;
        });
        // The generated client outlives this injector; its interceptor must not keep naming this context's tenant.
        inject(DestroyRef).onDestroy(() => {
          hubClient.interceptors.request.eject(registration);
          hubClient.interceptors.response.eject(responses);
        });
      },
    },
    {
      provide: EVENT_SOURCE_FACTORY,
      useFactory: (): EventSourceFactory => {
        const context = inject(TenantContext);
        const loss = inject(TenantLoss);
        return (url) => {
          const headers = tenantHeaders(context);
          const tenant = headers[TENANT_HEADER];
          return new FetchEventSource(url, headers, (status) => {
            if (status === 404 && tenant !== undefined) loss.notFound(tenant);
          });
        };
      },
    },
  ]);
}
