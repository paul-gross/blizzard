import { injectQuery } from '@tanstack/angular-query-experimental';

import { type UserView, listUsersApiUsersGet, hubUsersKey } from 'fleet';

/**
 * `GET /api/users` — the admin page's own user listing, gated on
 * `user:manage` hub-side (a `403` under this permission renders as this query's own
 * error state). Not in the SSE event vocabulary, so it carries no live-invalidation
 * wiring.
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
