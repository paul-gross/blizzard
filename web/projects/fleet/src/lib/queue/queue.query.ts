import { injectQuery } from '@tanstack/angular-query-experimental';

import { type QueuePeekEntry, peekQueueApiQueuePeekGet } from '../api/hub';
import { hubQueueKey } from '../query-keys';

/**
 * Hub `GET /api/queue/peek` read — the ready queue in the hub's explicit
 * reorder + grouping order, through TanStack Query and the generated hub
 * client (bzh:generated-client). Each entry carries its `position`, `graph_id`,
 * and PM pointers so the board can render and reshape the queue. The live-update
 * service re-peeks this on `queue-changed`/`chunk-changed`; the poll is the floor.
 */
export function injectHubQueueQuery() {
  return injectQuery(() => ({
    queryKey: hubQueueKey,
    queryFn: async (): Promise<QueuePeekEntry[]> => {
      const { data, error } = await peekQueueApiQueuePeekGet({ throwOnError: false });
      if (error) throw error;
      return data?.entries ?? [];
    },
    refetchInterval: 5000,
  }));
}
