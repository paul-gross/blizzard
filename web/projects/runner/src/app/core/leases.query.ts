import { injectQuery } from '@tanstack/angular-query-experimental';
import { RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS, runnerApi } from 'fleet';

import { injectActiveRunner } from './active-runner';
import { runnerLeasesKey } from './query-keys';

/**
 * The active runner's `GET /runners/{runner_id}/api/leases` read — the hub-free local surface: every
 * active lease with its joined binding facts and read-time-derived state
 * (`running`/`stale`/`parked`/`spawning`/`exited`), through TanStack Query and the
 * generated runner client (bzh:generated-client). Modeled on `fleet`'s
 * `injectHubRunnersQuery`. Covered by `lease-changed` (`runner-live-updates.ts`'s
 * `RUNNER_EVENT_INVALIDATION_REGISTRY`); lease staleness is
 * also *derived from elapsed heartbeat time*, which no event marks, so a lease
 * that goes quiet without a further transition needs a real re-read to flip
 * `stale` on the client's own clock — the interval below is that backstop,
 * not the primary signal a fresh lease/transition/close is covered by.
 */
export function injectRunnerLeasesQuery() {
  const runnerId = injectActiveRunner();
  return injectQuery(() => {
    const id = runnerId();
    return {
      queryKey: runnerLeasesKey(id),
      enabled: id !== null,
      queryFn: async (): Promise<runnerApi.LeaseView[]> => {
        const { data, error } = await runnerApi.listLeasesRunnersRunnerIdApiLeasesGet({
          path: { runner_id: id! },
          throwOnError: false,
        });
        if (error) throw error;
        return data?.items ?? [];
      },
      // See RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS.
      refetchInterval: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
    };
  });
}
