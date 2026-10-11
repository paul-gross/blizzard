import { injectQuery } from '@tanstack/angular-query-experimental';

import { type MemberView, listMembersApiMembersGet, hubMembersKey } from 'fleet';

/**
 * `GET /api/members` — the members of the tenant the board acts in, each with the role changes and the revocation the
 * signed-in actor may make to it. Gated on `user:manage` hub-side: a `403` below it renders as this query's own error
 * state. No SSE event names a membership change, so it carries no live-invalidation wiring; the membership mutations
 * invalidate it directly. `enabled` holds the read back while it is false — a caller that only names members, for an
 * actor who may lack `user:manage`.
 */
export function injectMembersQuery(enabled: () => boolean = () => true) {
  return injectQuery(() => ({
    queryKey: hubMembersKey,
    enabled: enabled(),
    queryFn: async (): Promise<MemberView[]> => {
      const { data, error } = await listMembersApiMembersGet({ throwOnError: false });
      if (error) throw error;
      return data ?? [];
    },
  }));
}
