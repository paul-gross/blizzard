import { injectQuery } from '@tanstack/angular-query-experimental';

import { type EventView, listEventsApiEventsGet, LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet';
import type { EventLogEntry, KnownEventSeverity } from './event-log.domain';
import { fromEventView, toEventLogSeverity } from './event-log.translate';
import { hubEventsKey, lensedKey } from '../core/query-keys';
import { injectProjectFilter } from '../core/project-filter';

/** The event feed's filter axes — `null`/`undefined` on any of them means
 * "unfiltered" for that axis, matching the hub's own query-param contract. */
export interface HubEventsFilters {
  readonly severity?: KnownEventSeverity | null;
  readonly runnerId?: string | null;
  readonly chunkId?: string | null;
}

/**
 * Hub `GET /api/events` read — the operational event feed, through
 * TanStack Query and the generated hub client (bzh:generated-client). `filters`
 * is a function so a caller-owned signal set recomputes it reactively; each
 * distinct filter combination rides in the query key, so it caches as its own
 * entry — same idiom as {@link injectHubFleetSpendDomainQuery}'s `since` window. The project lens is one more axis, read
 * from the shell rather than the caller: under a lens the read sends `?project=` (the hub keeps the events of the
 * project's chunks and of the runners that serve it) and keys on it.
 *
 * Freshness: `EVENT_INVALIDATION_REGISTRY` (`web/projects/hub/src/app/core/live/hub-live-updates.ts`).
 */
export function injectHubEventsQuery(filters: () => HubEventsFilters = () => ({})) {
  const projectFilter = injectProjectFilter();
  return injectQuery(() => {
    const f = filters();
    const project = projectFilter();
    return {
      queryKey: lensedKey([...hubEventsKey, f.severity ?? null, f.runnerId ?? null, f.chunkId ?? null], project),
      queryFn: async (): Promise<EventView[]> => {
        const { data, error } = await listEventsApiEventsGet({
          query: {
            severity: f.severity ? toEventLogSeverity(f.severity) : undefined,
            runner_id: f.runnerId ?? undefined,
            chunk_id: f.chunkId ?? undefined,
            project: project ?? undefined,
          },
          throwOnError: false,
        });
        if (error) throw error;
        return data?.events ?? [];
      },
      select: (wire: EventView[]): readonly EventLogEntry[] => wire.map(fromEventView),
      refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
    };
  });
}
