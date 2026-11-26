import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { enableProjectScopeApiProjectsProjectScopesSlugEnablePost, retireProjectScopeApiProjectsProjectScopesSlugRetirePost } from 'fleet';
import { scopeLifecycleMutationKey } from '../../core/mutation-keys';
import { gardenProject } from '../core/garden-project';
import { hubScopesKey } from '../../core/query-keys';
import { injectProjectFilter } from '../../core/project-filter';

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
 * (bzh:generated-client). On success it re-reads the scope list.
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
        body: {},
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubScopesKey }),
  }));
}
