import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  listProjectScopeRoutinesApiProjectsProjectScopesSlugRoutinesGet,
  listProjectScopesApiProjectsProjectScopesGet,
  listScopesApiScopesGet,
  type ScopeView,
  hubScopeRoutinesKey,
  hubScopesKey,
} from 'fleet';
import { injectProjectFilter, lensedKey } from 'fleet/shell';

/**
 * The scope list — every scope, newest first, each marked retired or not. Under a project lens it is that project's
 * scopes (`GET /api/projects/{project}/scopes`); on All it is the tenant's (`GET /api/scopes`), which only the
 * gardening doorway reads, grouping them by project. Feeds the gardening run dialog's scope picker and the routines
 * panel's scope list. Scopes change rarely and carry no SSE event of their own, `injectHubRoutinesQuery`'s own
 * standing.
 *
 * `tenantWide` reads the whole tenant even under a lens, for a figure that sets one project beside the others.
 */
export function injectHubScopesQuery(options: { readonly tenantWide?: boolean } = {}) {
  const filter = injectProjectFilter();
  const project = options.tenantWide === true ? () => null : filter;
  return injectQuery(() => {
    const lens = project();
    return {
      queryKey: lensedKey(hubScopesKey, lens),
      queryFn: async (): Promise<ScopeView[]> => {
        const { data, error } =
          lens === null
            ? await listScopesApiScopesGet({ throwOnError: false })
            : await listProjectScopesApiProjectsProjectScopesGet({ path: { project: lens }, throwOnError: false });
        if (error) throw error;
        return data ?? [];
      },
    };
  });
}

/**
 * `GET /api/projects/{project}/scopes/{slug}/routines` — every routine id linked to one of the lensed project's
 * scopes, the reverse of {@link injectHubRoutineScopesQuery}. A slug names a scope only inside its project, so the read
 * waits for a lens; disabled while `scopeSlug()` is `null`, the same rest state that query carries.
 */
export function injectHubScopeRoutinesQuery(scopeSlug: () => string | null) {
  const project = injectProjectFilter();
  return injectQuery(() => {
    const slug = scopeSlug();
    const lens = project();
    return {
      queryKey: lensedKey(hubScopeRoutinesKey(slug), lens),
      enabled: slug !== null && lens !== null,
      queryFn: async (): Promise<string[]> => {
        const { data, error } = await listProjectScopeRoutinesApiProjectsProjectScopesSlugRoutinesGet({
          path: { project: lens!, slug: slug! },
          throwOnError: false,
        });
        if (error) throw error;
        return data ?? [];
      },
    };
  });
}
