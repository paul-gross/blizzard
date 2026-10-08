import { injectQuery } from '@tanstack/angular-query-experimental';

import { type EventView, hubApi, listEventsApiEventsGet, LIVE_COVERED_POLL_BACKSTOP_MS, hubEventsKey } from 'fleet';

/** `value` narrowed to the generated severity vocabulary (`GET /api/events`'s own query
 * param), or `null` outside the closed set — so a filter value crossing from a generic UI
 * string into the typed query narrows against the wire's own values. */
export function narrowEventSeverity(value: string): hubApi.EventLogSeverity | null {
  return Object.values(hubApi.EventLogSeverity).find((severity) => severity === value) ?? null;
}

/** The event feed's filter axes — `null`/`undefined` on any of them means
 * "unfiltered" for that axis, matching the hub's own query-param contract. */
export interface HubEventsFilters {
  readonly severity?: hubApi.EventLogSeverity | null;
  readonly runnerId?: string | null;
  readonly chunkId?: string | null;
}

/**
 * Hub `GET /api/events` read — the operational event feed, through
 * TanStack Query and the generated hub client (bzh:generated-client). `filters`
 * is a function so a caller-owned signal set recomputes it reactively; each
 * distinct filter combination rides in the query key, so it caches as its own
 * entry — same idiom as {@link injectHubFleetSpendQuery}'s `since` window.
 *
 * Freshness: `EVENT_INVALIDATION_REGISTRY` (`web/projects/fleet/src/lib/sse/fleet-live.ts`).
 */
export function injectHubEventsQuery(filters: () => HubEventsFilters = () => ({})) {
  return injectQuery(() => {
    const f = filters();
    return {
      queryKey: [...hubEventsKey, f.severity ?? null, f.runnerId ?? null, f.chunkId ?? null],
      queryFn: async (): Promise<EventView[]> => {
        const { data, error } = await listEventsApiEventsGet({
          query: {
            severity: f.severity ?? undefined,
            runner_id: f.runnerId ?? undefined,
            chunk_id: f.chunkId ?? undefined,
          },
          throwOnError: false,
        });
        if (error) throw error;
        return data?.events ?? [];
      },
      refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
    };
  });
}
