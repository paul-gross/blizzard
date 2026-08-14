import { injectQuery } from '@tanstack/angular-query-experimental';

import { type QueuePeekEntry, getQueueApiQueueGet } from '../api/hub';
import { LIVE_COVERED_POLL_BACKSTOP_MS } from '../polling';
import { hubQueueKey } from '../query-keys';

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
