import { injectQuery } from '@tanstack/angular-query-experimental';

import { healthApiHealthGet } from '../api/hub';
import { hubHealthKey } from '../query-keys';

/**
 * Hub `/api/health` read, through TanStack Query and the generated hub client
 * (D-097/D-100). This is the plumbing proof for the read path: request/response
 * reads go through the query cache, and the request itself is the openapi-ts
 * client's typed SDK call — never hand-written fetch (bzh:generated-client). No
 * fake data; the query hits the daemon the app is served from.
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
