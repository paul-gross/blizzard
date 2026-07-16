import { injectQuery } from '@tanstack/angular-query-experimental';
import { runnerApi } from 'fleet';

import { runnerLeasesKey } from './query-keys';

/**
 * Runner `GET /api/leases` read — the hub-free local surface (issue #28): every
 * active lease with its joined binding facts and read-time-derived state
 * (`running`/`stale`/`parked`/`spawning`/`exited`), through TanStack Query and the
 * generated runner client (bzh:generated-client). Modeled on `fleet`'s
 * `injectHubRunnersQuery`; `refetchInterval: 5000` matches its floor. There is no
 * SSE here — the runner has no event stream, so the poll is the only signal.
 */
export function injectRunnerLeasesQuery() {
  return injectQuery(() => ({
    queryKey: runnerLeasesKey,
    queryFn: async (): Promise<runnerApi.LeaseView[]> => {
      const { data, error } = await runnerApi.listLeasesApiLeasesGet({ throwOnError: false });
      if (error) throw error;
      return data?.items ?? [];
    },
    refetchInterval: 5000,
  }));
}
