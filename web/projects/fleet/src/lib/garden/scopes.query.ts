import { injectQuery } from '@tanstack/angular-query-experimental';

import { listScopesApiScopesGet, type ScopeView } from '../api/hub';
import { hubScopesKey } from '../query-keys';

/**
 * Hub `GET /api/scopes` read — every scope, newest first, each marked retired or
 * not. Feeds both the gardening run dialog's scope picker and the routines panel's
 * scope list. Scopes change rarely and carry no SSE event of their own,
 * `injectHubRoutinesQuery`'s own standing.
 */
export function injectHubScopesQuery() {
  return injectQuery(() => ({
    queryKey: hubScopesKey,
    queryFn: async (): Promise<ScopeView[]> => {
      const { data, error } = await listScopesApiScopesGet({ throwOnError: false });
      if (error) throw error;
      return data ?? [];
    },
  }));
}
