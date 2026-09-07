import { injectQuery } from '@tanstack/angular-query-experimental';

import { listScopeRoutinesApiScopesSlugRoutinesGet, listScopesApiScopesGet, type ScopeView } from '../api/hub';
import { hubScopeRoutinesKey, hubScopesKey } from '../query-keys';

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

/**
 * Hub `GET /api/scopes/{slug}/routines` read — every routine id linked to a scope,
 * the reverse of {@link injectHubRoutineScopesQuery}. Disabled while `scopeSlug()`
 * is `null`, the same rest state that query carries.
 */
export function injectHubScopeRoutinesQuery(scopeSlug: () => string | null) {
  return injectQuery(() => {
    const slug = scopeSlug();
    return {
      queryKey: hubScopeRoutinesKey(slug),
      enabled: slug !== null,
      queryFn: async (): Promise<string[]> => {
        const { data, error } = await listScopeRoutinesApiScopesSlugRoutinesGet({
          path: { slug: slug! },
          throwOnError: false,
        });
        if (error) throw error;
        return data ?? [];
      },
    };
  });
}
