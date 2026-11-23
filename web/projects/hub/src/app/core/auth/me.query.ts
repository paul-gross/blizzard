import { inject } from '@angular/core';
import { injectQuery } from '@tanstack/angular-query-experimental';

import { type MeResponse, meApiMeGet } from 'fleet/shell';

import { type Session, inSelectedTenant } from './session.domain';
import { fromMeResponse } from './session.translate';
import { TenantContext } from './tenant-context';
import { hubMeKey } from '../query-keys';

/**
 * `GET /api/me` — the resolved identity + its server-expanded permission set.
 * This is the **one** place the client reads the current user.
 *
 * A **401** resolves the query to `null`; any other failure surfaces through
 * TanStack's error state. `retry: false` settles an expired session in one round trip.
 *
 * It hands out the {@link Session} in the board's tenant: `permissions` are the selected membership's
 * ({@link inSelectedTenant}), so every permission check reads the tenant the board acts in. The memberships
 * themselves (`tenants`) pass through as the hub answered them.
 */
export function injectMeQuery() {
  const tenant = inject(TenantContext);
  return injectQuery(() => {
    const resolve = tenant.resolver();
    return {
      queryKey: hubMeKey,
      queryFn: readMe,
      select: (me: MeResponse | null) => {
        if (me === null) return null;
        const session = fromMeResponse(me);
        return inSelectedTenant(session, resolve(session.tenants));
      },
      retry: false,
    };
  });
}

/** One read of `GET /api/me`, as the hub answered it — `null` for no session (a 401), a thrown error for any other
 * failure. {@link injectMeQuery}'s fetch, and a caller's that must see the memberships as they are now, outside the
 * query cache. */
export async function readMe(): Promise<MeResponse | null> {
  const { data, error, response } = await meApiMeGet({ throwOnError: false });
  if (response?.status === 401) return null;
  if (error) throw error;
  return data ?? null;
}

/** {@link readMe} as a {@link Session}, for a caller outside the query cache that reads the memberships as they are
 * now. */
export async function readSession(): Promise<Session | null> {
  const me = await readMe();
  return me === null ? null : fromMeResponse(me);
}

export { hasPermission } from './session.domain';
