import { injectQuery } from '@tanstack/angular-query-experimental';

import { type BacklogPeekEntry, type QueuePeekEntry, getBacklogApiBacklogGet, getQueueApiQueueGet, LIVE_COVERED_POLL_BACKSTOP_MS, hubBacklogKey, hubQueueKey } from 'fleet';
import { DRAIN_LIMIT, drainPages } from '../../core/paginated-read';

/**
 * Hub `GET /api/queue` read — the ready queue in the hub's explicit reorder +
 * grouping order, through TanStack Query and the generated hub client
 * (bzh:generated-client). Each entry carries its `position`, `graph_id`, and work
 * refs so the board can render and reshape the queue. The read is keyset-paginated
 * on the hub; {@link drainPages} follows `next_cursor` to exhaustion,
 * so each entry's `position` still reads as its absolute index in the whole order.
 * Freshness: `EVENT_INVALIDATION_REGISTRY` (`web/projects/fleet/src/lib/sse/fleet-live.ts`).
 */
export function injectHubQueueQuery() {
  return injectQuery(() => ({
    queryKey: hubQueueKey,
    queryFn: (): Promise<QueuePeekEntry[]> =>
      drainPages(
        (cursor) => getQueueApiQueueGet({ query: { cursor, limit: DRAIN_LIMIT }, throwOnError: false }),
        (page) => page.entries,
      ),
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

/**
 * Hub `GET /api/backlog` read — the `not_ready` list in the hub's explicit
 * reorder order, through TanStack Query and the generated hub client
 * (bzh:generated-client). Unlike the ready queue's read, the backlog's requires
 * `queue:reorder`: an operator triage surface, not fleet-wide visibility, so
 * `canReorder` gates this query's `enabled` — the read must never fire and then
 * discard a 403, it must not fire at all without the permission. Pass the
 * identity's `queue:reorder` check as a reactive accessor.
 * Each entry carries its `position`, `graph_id`, and work refs so the board can
 * render and reshape the backlog. The read is keyset-paginated on the hub;
 * {@link drainPages} follows `next_cursor` to exhaustion, the same
 * as {@link injectHubQueueQuery}. Freshness: `EVENT_INVALIDATION_REGISTRY` (`web/projects/fleet/src/lib/sse/fleet-live.ts`).
 */
export function injectHubBacklogQuery(canReorder: () => boolean) {
  return injectQuery(() => ({
    queryKey: hubBacklogKey,
    enabled: canReorder(),
    queryFn: (): Promise<BacklogPeekEntry[]> =>
      drainPages(
        (cursor) => getBacklogApiBacklogGet({ query: { cursor, limit: DRAIN_LIMIT }, throwOnError: false }),
        (page) => page.entries,
      ),
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}
