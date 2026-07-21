import { injectQuery } from '@tanstack/angular-query-experimental';

import { type UserView, listUsersApiUsersGet } from '../api/hub';
import { hubUsersKey } from '../query-keys';

/**
 * `GET /api/users` — the admin page's own user listing (issue #94), gated on
 * `user:manage` hub-side (a `403` under this permission renders as this query's own
 * error state; the page itself is nav-gated before it ever mounts, `app-nav.ts`).
 * Not in the SSE event vocabulary — a role change is rare and the assignment
 * mutation invalidates this key directly, so no live-invalidation wiring is needed.
 */
export function injectUsersQuery() {
  return injectQuery(() => ({
    queryKey: hubUsersKey,
    queryFn: async (): Promise<UserView[]> => {
      const { data, error } = await listUsersApiUsersGet({ throwOnError: false });
      if (error) throw error;
      return data ?? [];
    },
  }));
}
