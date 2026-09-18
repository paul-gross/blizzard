import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { editScopeApiScopesSlugPatch } from '../api/hub';
import { editScopeMutationKey } from '../mutation-keys';
import { hubScopesKey } from '../query-keys';

/** Change a scope's stored description in place — never touches its slug. */
export interface ScopeEditVars {
  readonly slug: string;
  readonly description: string;
}

/**
 * `PATCH /api/scopes/{slug}` with `{ description }` — through the generated client
 * (bzh:generated-client), `injectSetChunkGraphMutation`'s own text-input-and-Set
 * shape. On success it re-reads the scope list, since it is every consumer's only
 * scope read today.
 */
export function injectEditScopeMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: editScopeMutationKey,
    mutationFn: async (vars: ScopeEditVars): Promise<void> => {
      const { error } = await editScopeApiScopesSlugPatch({
        path: { slug: vars.slug },
        body: { description: vars.description },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubScopesKey }),
  }));
}
