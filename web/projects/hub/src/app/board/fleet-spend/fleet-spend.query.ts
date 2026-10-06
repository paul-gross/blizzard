import { injectQuery } from '@tanstack/angular-query-experimental';

import { fleetSpendApiSpendGet, type FleetSpendView, LIVE_COVERED_POLL_BACKSTOP_MS, hubFleetSpendKey } from 'fleet/shell';

/**
 * Hub `GET /api/spend?since=&until=` read — the fleet-wide usage/cost total over a
 * caller-chosen window, through TanStack Query
 * and the generated hub client. `since`/`until` are functions so the caller can
 * recompute them (e.g. local start-of-day rolling over) without re-wiring the query;
 * both ride in the query key so a new window is its own cache entry — `until` included,
 * or two windows sharing a `since` but differing in `until` would collide on one entry.
 * `until` is omitted from the request when the accessor returns `undefined` — an
 * open-ended tail.
 */
export function injectHubFleetSpendQuery(since: () => string, until: () => string | undefined = () => undefined) {
  return injectQuery(() => ({
    queryKey: [...hubFleetSpendKey, since(), until()],
    queryFn: async (): Promise<FleetSpendView> => {
      const { data, error } = await fleetSpendApiSpendGet({
        query: { since: since(), until: until() },
        throwOnError: false,
      });
      if (error) throw error;
      return data as FleetSpendView;
    },
    // Covered by chunk-changed (EVENT_INVALIDATION_REGISTRY, sse/fleet-live.ts) — usage
    // rides the same fact a chunk-changed frame reports. See LIVE_COVERED_POLL_BACKSTOP_MS.
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}
