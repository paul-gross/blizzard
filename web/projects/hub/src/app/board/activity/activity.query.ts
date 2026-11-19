import { injectQuery } from '@tanstack/angular-query-experimental';

import { type ActivityView, listActivityApiActivityGet } from 'fleet';
import { type ActivityFact } from 'fleet/shell';
import { hubActivityKey, lensedKey } from '../../core/query-keys';
import { injectProjectFilter } from '../../core/project-filter';

/**
 * The backfill row cap for `GET /api/activity` — reconciled with
 * the backend's own `limit` default so a reader who goes looking for "why 200" finds
 * one number, not two that happen to agree by coincidence.
 */
export const ACTIVITY_LIMIT = 200;

/** Translate the durable read at the boundary into the same facts the live feed uses. */
export function translateActivityRow(row: ActivityView): ActivityFact {
  const { at, type, key, ...data } = row;
  return { at: Date.parse(at), type, key, data: { ...data, key } };
}

/**
 * Hub `GET /api/activity` read — the Activity feed panel's backfill-on-load,
 * through TanStack Query and the generated hub client
 * (bzh:generated-client). Takes no filter accessor, unlike {@link injectHubEventsQuery}: the rail shows the whole
 * recent feed, narrowed only by the project lens — under a lens the read sends `?project=` (the hub keeps the rows of the
 * project's chunks and of the runners that serve it) and keys on it.
 *
 * `limit` is hardcoded to {@link ACTIVITY_LIMIT} rather than left to the generated
 * client's own default, so the backfill cap and the panel's rendered-row cap
 * (`activity-panel.ts`) stay the same number in one place.
 *
 * No `refetchInterval`: this is a one-shot backfill, not a poll — the live SSE tee
 * ({@link HubLiveUpdates}) is what keeps the feed current after the initial read.
 */
export function injectHubActivityQuery() {
  const projectFilter = injectProjectFilter();
  return injectQuery(() => {
    const project = projectFilter();
    return {
      queryKey: lensedKey(hubActivityKey, project),
      queryFn: async (): Promise<ActivityFact[]> => {
        const { data, error } = await listActivityApiActivityGet({
          query: { limit: ACTIVITY_LIMIT, project: project ?? undefined },
          throwOnError: false,
        });
        if (error) throw error;
        return (data?.activity ?? []).map(translateActivityRow);
      },
    };
  });
}
