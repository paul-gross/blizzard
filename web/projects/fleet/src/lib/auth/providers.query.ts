import { injectQuery } from '@tanstack/angular-query-experimental';

import { type ProviderSummary, listProvidersApiAuthProvidersGet } from '../api/hub';
import { hubAuthProvidersKey } from '../query-keys';

/**
 * `GET /api/auth/providers` — the configured login-provider list the login page
 * renders one button per entry from (issue #93). Empty under `auth.mode = "none"`
 * (the hub's own answer, never re-derived client-side) — the login page reads that
 * directly rather than asking the config for its own mode.
 */
export function injectAuthProvidersQuery() {
  return injectQuery(() => ({
    queryKey: hubAuthProvidersKey,
    queryFn: async (): Promise<readonly ProviderSummary[]> => {
      const { data, error } = await listProvidersApiAuthProvidersGet({ throwOnError: false });
      if (error) throw error;
      return data ?? [];
    },
  }));
}
