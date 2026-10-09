import { inject } from '@angular/core';
import { injectQuery } from '@tanstack/angular-query-experimental';

import { type MeResponse, meApiMeGet, hubMeKey } from 'fleet/shell';

import { TenantContext, inSelectedTenant } from './tenant-context';

/**
 * `GET /api/me` — the resolved identity + its server-expanded permission set.
 * This is the **one** place the client reads the current user; the
 * role → permission map itself is never re-typed here (`bzh:generated-client`'s
 * spirit applied to authz: the hub computes it, `/api/me` carries it).
 *
 * A **401** (no/expired session) resolves the query to `null` rather than an error
 * state — "not authenticated" is a legitimate, expected value here, not a fault. Any
 * other failure still surfaces through TanStack's error state. `retry: false` so an
 * expired session settles in one round trip rather than TanStack's default backoff
 * retries (the 401 response itself is handled in `auth.interceptor.ts`).
 *
 * The identity it hands out is the one in the board's tenant: `role` and `permissions` are the selected
 * membership's ({@link inSelectedTenant}), so every permission check reads the tenant the board acts in. The
 * memberships themselves (`tenants`) and `hub_admin` pass through as the hub answered them.
 */
export function injectMeQuery() {
  const tenant = inject(TenantContext);
  return injectQuery(() => {
    const resolve = tenant.resolver();
    return {
      queryKey: hubMeKey,
      queryFn: readMe,
      select: (me: MeResponse | null) => (me === null ? null : inSelectedTenant(me, resolve(me.tenants ?? []))),
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

/** One permission `/api/me` can carry — the generated `Permission` vocabulary. */
type Permission = MeResponse['permissions'][number];

/** Whether a resolved identity carries `permission` — the single selector every
 * capability-gated control reads (`hasPermission(me(), 'user:manage')`) rather than
 * re-deriving its own role check. `null`/`undefined` (unauthenticated, or not yet
 * resolved) never carries a permission. */
export function hasPermission(
  me: MeResponse | null | undefined,
  permission: Permission,
): boolean {
  return me !== null && me !== undefined && me.permissions.includes(permission);
}
