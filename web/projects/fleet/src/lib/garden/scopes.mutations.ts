import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { type ScopeView, createScopeApiScopesPost } from '../api/hub';
import { hubScopesKey } from '../query-keys';

/** Mint a scope through `POST /api/scopes` — a create-or-no-op onto an existing slug
 * (D4). The gardening run dialog's own create-then-run ordering (D3) is
 * `GardeningRunDialog.onSubmit`'s own fact, not restated here. */
export interface ScopeCreateVars {
  readonly slug: string;
  readonly description: string;
}

/**
 * `POST /api/scopes` through the generated client (bzh:generated-client). On success,
 * invalidates the scope picker read so a freshly-minted slug appears without a reload.
 */
export function injectCreateScopeMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: ScopeCreateVars): Promise<ScopeView> => {
      const { data, error } = await createScopeApiScopesPost({
        body: { slug: vars.slug, description: vars.description },
        throwOnError: false,
      });
      if (error) throw error;
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: hubScopesKey });
    },
  }));
}
