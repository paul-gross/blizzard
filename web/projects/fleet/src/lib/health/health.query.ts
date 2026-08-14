import { injectQuery } from '@tanstack/angular-query-experimental';

import { healthApiHealthGet } from '../api/hub';
import { hubHealthKey } from '../query-keys';

/**
 * Hub `/api/health` read, through TanStack Query and the generated hub client.
 * This is the plumbing proof for the read path: request/response
 * reads go through the query cache, and the request itself is the openapi-ts
 * client's typed SDK call — never hand-written fetch (bzh:generated-client). No
 * fake data; the query hits the daemon the app is served from.
 *
 * Kept at its short fixed interval, deliberately not widened to the SSE-covered
 * backstop the other seven board queries carry (issue #316): no live event covers
 * this data because this *is* the read whose whole purpose is "is the connection to
 * the hub still good" — a health floor answers that question even for a caller with
 * no SSE stream open at all, and widening it would just make a dead hub take longer
 * to notice.
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
