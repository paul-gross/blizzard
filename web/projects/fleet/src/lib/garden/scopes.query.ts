import { injectQuery } from '@tanstack/angular-query-experimental';

import { type ScopeView, listScopesApiScopesGet } from '../api/hub';
import { hubScopesKey } from '../query-keys';

/**
 * Hub `GET /api/scopes` read — every scope, through TanStack Query and the generated
 * hub client (bzh:generated-client). Feeds the gardening run dialog's scope picker.
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
