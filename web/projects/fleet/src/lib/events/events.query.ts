import { injectQuery } from '@tanstack/angular-query-experimental';

import { type EventView, listEventsApiEventsGet } from '../api/hub';
import { LIVE_COVERED_POLL_BACKSTOP_MS } from '../polling';
import { hubEventsKey } from '../query-keys';

/** The severity vocabulary's closed wire set (`GET /api/events`'s own query param) —
 * declared once here so a filter value crossing from a generic UI string into the
 * typed query narrows against this list rather than an ad hoc per-value check. */
export const EVENT_SEVERITIES = ['critical', 'warning', 'info'] as const;
export type EventSeverity = (typeof EVENT_SEVERITIES)[number];

/** `value` narrowed to {@link EventSeverity}, or `null` outside the closed set. */
export function narrowEventSeverity(value: string): EventSeverity | null {
  return (EVENT_SEVERITIES as readonly string[]).includes(value) ? (value as EventSeverity) : null;
}

/** The event feed's filter axes — `null`/`undefined` on any of them means
 * "unfiltered" for that axis, matching the hub's own query-param contract. */
export interface HubEventsFilters {
  readonly severity?: EventSeverity | null;
  readonly runnerId?: string | null;
  readonly chunkId?: string | null;
}

/**
 * Hub `GET /api/events` read — the operational event feed (Phase 4), through
 * TanStack Query and the generated hub client (bzh:generated-client). `filters`
 * is a function so a caller-owned signal set recomputes it reactively; each
 * distinct filter combination rides in the query key, so it caches as its own
 * entry — same idiom as {@link injectHubFleetSpendQuery}'s `since` window.
 *
 * The live-update service re-reads this on `event-logged`, and on an
 * escalation-bearing `chunk-changed`; the poll is a backstop (issue #316), not the
 * primary freshness path.
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
      // Covered by event-logged and an escalation-bearing chunk-changed
      // (EVENT_INVALIDATION_REGISTRY, sse/fleet-live.ts). See LIVE_COVERED_POLL_BACKSTOP_MS.
      refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
    };
  });
}
