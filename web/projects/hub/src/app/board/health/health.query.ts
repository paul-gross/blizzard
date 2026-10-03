import { injectQuery } from '@tanstack/angular-query-experimental';

import { healthApiHealthGet, hubHealthKey } from 'fleet/shell';

/**
 * Hub `/api/health` read, through TanStack Query and the generated hub client.
 * This is the plumbing proof for the read path: request/response
 * reads go through the query cache, and the request itself is the openapi-ts
 * client's typed SDK call — never hand-written fetch (bzh:generated-client). No
 * fake data; the query hits the daemon the app is served from.
 *
 * Polls on a short fixed interval, below the live-covered backstop — no live event
 * covers this read. Pinned by `health.query.spec.ts`'s "re-reads /api/health well inside
 * the live-covered backstop, with no SSE event".
 */
export function injectHubHealthQuery() {
  return injectQuery(() => ({
    queryKey: hubHealthKey,
    queryFn: async () => {
      const { data, error } = await healthApiHealthGet({ throwOnError: false });
      if (error) throw error;
      return data ?? {};
    },
    refetchInterval: 5000,
  }));
}
