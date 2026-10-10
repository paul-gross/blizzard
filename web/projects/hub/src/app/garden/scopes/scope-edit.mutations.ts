import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { editProjectScopeApiProjectsProjectScopesSlugPatch, hubScopesKey } from 'fleet';
import { injectProjectFilter } from 'fleet/shell';
import { editScopeMutationKey } from '../../core/mutation-keys';
import { gardenProject } from '../core/garden-project';

/** Change a scope's stored description in place — never touches its slug. */
export interface ScopeEditVars {
  readonly slug: string;
  readonly description: string;
}

/**
 * `PATCH /api/projects/{project}/scopes/{slug}` with `{ description }`, inside the lensed project, — through the generated client
 * (bzh:generated-client), `injectSetChunkGraphMutation`'s own text-input-and-Set
 * shape. On success it re-reads the scope list, since it is every consumer's only
 * scope read today.
 */
export function injectEditScopeMutation() {
  const queryClient = inject(QueryClient);
  const project = injectProjectFilter();
  return injectMutation(() => ({
    mutationKey: editScopeMutationKey,
    mutationFn: async (vars: ScopeEditVars): Promise<void> => {
      const { error } = await editProjectScopeApiProjectsProjectScopesSlugPatch({
        path: { project: gardenProject(project()), slug: vars.slug },
        body: { description: vars.description },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubScopesKey }),
  }));
}
