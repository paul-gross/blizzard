import { injectQuery } from '@tanstack/angular-query-experimental';

import { type MeResponse, meApiMeGet } from '../api/hub';
import { hubMeKey } from '../query-keys';

/**
 * `GET /api/me` — the resolved identity + its server-expanded permission set (issue
 * #93). This is the **one** place the client reads the current user; the
 * role → permission map itself is never re-typed here (`bzh:generated-client`'s
 * spirit applied to authz: the hub computes it, `/api/me` carries it).
 *
 * A **401** (no/expired session) resolves the query to `null` rather than an error
 * state — "not authenticated" is a legitimate, expected value here, not a fault. Any
 * other failure still surfaces through TanStack's error state. `retry: false` so an
 * expired session settles in one round trip rather than TanStack's default backoff
 * retries — the interceptor (`auth.interceptor.ts`) reacts to the same 401 by routing
 * to `/login`, which a silent retry loop would only delay.
 */
export function injectMeQuery() {
  return injectQuery(() => ({
    queryKey: hubMeKey,
    queryFn: async (): Promise<MeResponse | null> => {
      const { data, error, response } = await meApiMeGet({ throwOnError: false });
      if (response?.status === 401) return null;
      if (error) throw error;
      return data ?? null;
    },
    retry: false,
  }));
}

/** Whether a resolved identity carries `permission` — the single selector every
 * capability-gated control reads (`hasPermission(me(), 'user:manage')`) rather than
 * re-deriving its own role check. `null`/`undefined` (unauthenticated, or not yet
 * resolved) never carries a permission. */
export function hasPermission(me: MeResponse | null | undefined, permission: string): boolean {
  return me !== null && me !== undefined && me.permissions.includes(permission);
}
