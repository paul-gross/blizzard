import { injectQuery } from '@tanstack/angular-query-experimental';

import { type BacklogPeekEntry, type QueuePeekEntry, getBacklogApiBacklogGet, getQueueApiQueueGet } from '../api/hub';
import { LIVE_COVERED_POLL_BACKSTOP_MS } from '../polling';
import { hubBacklogKey, hubQueueKey } from '../query-keys';

/**
 * Hub `GET /api/queue` read — the ready queue in the hub's explicit reorder +
 * grouping order, through TanStack Query and the generated hub client
 * (bzh:generated-client). The `GET /api/queue/peek` alias was removed in issue #105,
 * so this is the board's only ready-queue read. Each entry carries its `position`, `graph_id`, and work
 * refs so the board can render and reshape the queue. The live-update
 * service re-reads this on `queue-changed`/`chunk-changed`; the poll is a backstop
 * (issue #316), not the primary freshness path.
 */
export function injectHubQueueQuery() {
  return injectQuery(() => ({
    queryKey: hubQueueKey,
    queryFn: async (): Promise<QueuePeekEntry[]> => {
      const { data, error } = await getQueueApiQueueGet({ throwOnError: false });
      if (error) throw error;
      return data?.entries ?? [];
    },
    // Covered by queue-changed and chunk-changed (EVENT_INVALIDATION_REGISTRY,
    // sse/fleet-live.ts). See LIVE_COVERED_POLL_BACKSTOP_MS.
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

/**
 * Hub `GET /api/backlog` read — the `not_ready` list in the hub's explicit
 * reorder order, through TanStack Query and the generated hub client
 * (bzh:generated-client). Unlike the ready queue's read, the backlog's requires
 * `queue:reorder` (`bzh:ranking-is-per-list`): it is an operator triage surface,
 * not fleet-wide visibility, so `canReorder` gates this query's `enabled` — the
 * read must never fire and then discard a 403, it must not fire at all without the
 * permission. Pass the identity's `queue:reorder` check as a reactive accessor, the
 * same shape {@link injectHubChunkWorkItemsQuery} takes its selected chunk id.
 * Each entry carries its `position`, `graph_id`, and work refs so the board can
 * render and reshape the backlog. The live-update service re-reads this on
 * `queue-changed`; the poll is a backstop (issue #316), not the primary
 * freshness path.
 */
export function injectHubBacklogQuery(canReorder: () => boolean) {
  return injectQuery(() => ({
    queryKey: hubBacklogKey,
    enabled: canReorder(),
    queryFn: async (): Promise<BacklogPeekEntry[]> => {
      const { data, error } = await getBacklogApiBacklogGet({ throwOnError: false });
      if (error) throw error;
      return data?.entries ?? [];
    },
    // Covered by queue-changed (EVENT_INVALIDATION_REGISTRY, sse/fleet-live.ts).
    // See LIVE_COVERED_POLL_BACKSTOP_MS.
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}
