import { injectQuery } from '@tanstack/angular-query-experimental';

import { fleetSpendApiFleetSpendGet, type FleetSpendView } from '../api/hub';
import { hubFleetSpendKey } from '../query-keys';

/**
 * Hub `GET /api/fleet/spend?since=` read — the fleet-wide usage/cost total since a
 * caller-chosen instant (issue #60), through TanStack Query and the generated hub
 * client. `since` is a function so the caller can recompute it (e.g. local
 * start-of-day rolling over) without re-wiring the query; it rides in the query key so
 * a new window is its own cache entry.
 */
export function injectHubFleetSpendQuery(since: () => string) {
  return injectQuery(() => ({
    queryKey: [...hubFleetSpendKey, since()],
    queryFn: async (): Promise<FleetSpendView> => {
      const { data, error } = await fleetSpendApiFleetSpendGet({ query: { since: since() }, throwOnError: false });
      if (error) throw error;
      return data as FleetSpendView;
    },
    refetchInterval: 3000,
  }));
}
