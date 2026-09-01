import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { enableScopeApiScopesSlugEnablePost, retireScopeApiScopesSlugRetirePost } from '../api/hub';
import { hubScopesKey } from '../query-keys';

/** Retire or re-enable a scope's reversible brake: a retired scope is
 * excluded from every picker, but its findings stay live, queryable, and attributable
 * throughout — mirrors `GraphLifecycleVars`. */
export interface ScopeLifecycleVars {
  readonly slug: string;
  readonly retired: boolean;
}

/**
 * `POST /api/scopes/{slug}/retire|enable` — routed by the desired `retired` state
 * (`injectGraphLifecycleMutation`'s own shape), through the generated client
 * (bzh:generated-client). On success it re-reads the scope list. `by` defaults to
 * `operator` server-side.
 */
export function injectScopeLifecycleMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: ScopeLifecycleVars): Promise<void> => {
      const call = vars.retired ? retireScopeApiScopesSlugRetirePost : enableScopeApiScopesSlugEnablePost;
      const { error } = await call({
        path: { slug: vars.slug },
        body: { by: 'operator' },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: hubScopesKey });
    },
  }));
}
