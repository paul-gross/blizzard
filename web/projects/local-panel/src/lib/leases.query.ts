import { injectQuery } from '@tanstack/angular-query-experimental';
import { runnerApi } from 'fleet';

import { RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS } from './polling';
import { runnerLeasesKey } from './query-keys';

/**
 * Runner `GET /api/leases` read — the hub-free local surface (issue #28): every
 * active lease with its joined binding facts and read-time-derived state
 * (`running`/`stale`/`parked`/`spawning`/`exited`), through TanStack Query and the
 * generated runner client (bzh:generated-client). Modeled on `fleet`'s
 * `injectHubRunnersQuery`. Covered by `lease-changed` (`runner-live-updates.ts`'s
 * `RUNNER_EVENT_INVALIDATION_REGISTRY`, blizzard#317 Phase 4); lease staleness is
 * also *derived from elapsed heartbeat time*, which no event marks, so a lease
 * that goes quiet without a further transition needs a real re-read to flip
 * `stale` on the client's own clock (D7) — the interval below is that backstop,
 * not the primary signal a fresh lease/transition/close is covered by.
 */
export function injectRunnerLeasesQuery() {
  return injectQuery(() => ({
    queryKey: runnerLeasesKey,
    queryFn: async (): Promise<runnerApi.LeaseView[]> => {
      const { data, error } = await runnerApi.listLeasesApiLeasesGet({ throwOnError: false });
      if (error) throw error;
      return data?.items ?? [];
    },
    // See RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS.
    refetchInterval: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}
