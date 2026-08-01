import { injectQuery } from '@tanstack/angular-query-experimental';

import { type ActivityView, listActivityApiActivityGet } from '../api/hub';
import { hubActivityKey } from '../query-keys';

/**
 * The backfill row cap for `GET /api/activity` (issue #213 Phase 4) — reconciled with
 * the backend's own `limit` default so a reader who goes looking for "why 200" finds
 * one number, not two that happen to agree by coincidence.
 */
export const ACTIVITY_LIMIT = 200;

/**
 * Hub `GET /api/activity` read — the Event log panel's backfill-on-load (issue #213
 * Phase 4), through TanStack Query and the generated hub client
 * (bzh:generated-client). Unfiltered (the rail always shows the whole recent feed), so
 * unlike {@link injectHubEventsQuery} this takes no filter accessor and rides one
 * fixed query key.
 *
 * `limit` is hardcoded to {@link ACTIVITY_LIMIT} rather than left to the generated
 * client's own default, so the backfill cap and the panel's rendered-row cap
 * (`event-log-panel.ts`) stay the same number in one place.
 *
 * No `refetchInterval`: this is a one-shot backfill, not a poll — the live SSE tee
 * ({@link FleetLiveUpdates}) is what keeps the feed current after the initial read.
 */
export function injectHubActivityQuery() {
  return injectQuery(() => ({
    queryKey: hubActivityKey,
    queryFn: async (): Promise<ActivityView[]> => {
      const { data, error } = await listActivityApiActivityGet({
        query: { limit: ACTIVITY_LIMIT },
        throwOnError: false,
      });
      if (error) throw error;
      return data?.activity ?? [];
    },
  }));
}
