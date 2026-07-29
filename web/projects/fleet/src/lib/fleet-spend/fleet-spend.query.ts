import { injectQuery } from '@tanstack/angular-query-experimental';

import { fleetSpendApiSpendGet, type FleetSpendView } from '../api/hub';
import { hubFleetSpendKey } from '../query-keys';

/**
 * Hub `GET /api/spend?since=&until=` read — the fleet-wide usage/cost total over a
 * caller-chosen window (issue #60, `until` added issue #183), through TanStack Query
 * and the generated hub client. `since`/`until` are functions so the caller can
 * recompute them (e.g. local start-of-day rolling over) without re-wiring the query;
 * both ride in the query key so a new window is its own cache entry — `until` included,
 * or two windows sharing a `since` but differing in `until` would collide on one entry.
 * `until` is omitted from the request when the accessor returns `undefined` — the
 * original open-ended tail.
 *
 * Relocated from `/api/fleet/spend` (issue #87): that prefix is now the
 * runner-authenticated fleet router, so the operator's anonymous spend read moved to
 * `/api/spend` to free the namespace.
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
    refetchInterval: 3000,
  }));
}
