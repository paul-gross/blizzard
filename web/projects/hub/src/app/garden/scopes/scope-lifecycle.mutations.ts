import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import {
  enableProjectScopeApiProjectsProjectScopesSlugEnablePost,
  retireProjectScopeApiProjectsProjectScopesSlugRetirePost,
  hubScopesKey,
} from 'fleet';
import { injectProjectFilter } from 'fleet/shell';
import { scopeLifecycleMutationKey } from '../../core/mutation-keys';
import { gardenProject } from '../core/garden-project';

/** Retire or re-enable a scope's reversible brake: a retired scope is
 * excluded from every picker, but its findings stay live, queryable, and attributable
 * throughout — mirrors `GraphLifecycleVars`. */
export interface ScopeLifecycleVars {
  readonly slug: string;
  readonly retired: boolean;
}

/**
 * `POST /api/projects/{project}/scopes/{slug}/retire|enable`, inside the lensed project — routed by the desired `retired` state
 * (`injectGraphLifecycleMutation`'s own shape), through the generated client
 * (bzh:generated-client). On success it re-reads the scope list. `by` defaults to
 * `operator` server-side.
 */
export function injectScopeLifecycleMutation() {
  const queryClient = inject(QueryClient);
  const project = injectProjectFilter();
  return injectMutation(() => ({
    mutationKey: scopeLifecycleMutationKey,
    mutationFn: async (vars: ScopeLifecycleVars): Promise<void> => {
      const call = vars.retired
        ? retireProjectScopeApiProjectsProjectScopesSlugRetirePost
        : enableProjectScopeApiProjectsProjectScopesSlugEnablePost;
      const { error } = await call({
        path: { project: gardenProject(project()), slug: vars.slug },
        body: { by: 'operator' },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubScopesKey }),
  }));
}
