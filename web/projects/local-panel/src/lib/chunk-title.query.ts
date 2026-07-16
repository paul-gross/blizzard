import { injectQuery } from '@tanstack/angular-query-experimental';
import { runnerApi } from 'fleet';

import { runnerChunkPmItemsKey } from './query-keys';

/**
 * Runner `GET /api/chunks/{chunk_id}/pm-items` read — the layered pass-through
 * (D-084: panel → its own runner → hub → vendor, with the hub's credentials) that
 * carries the issue title layered onto a lease row (issue #28). This is a strictly
 * **severable, volatile** read, never the panel's critical path: the leases route
 * (`leases.query.ts`) is hub-free and this is not, so its failure must never touch
 * the leases read, the list, or any other row.
 *
 * The config below **is** the severability guarantee, not incidental tuning:
 * - `refetchInterval: false` — never polls. A hub outage costs one failed request
 *   per chunk id, not one per row every 5s forever.
 * - `retry: false` — no exponential retry storm stacked on top of that one request.
 * - `staleTime`/`gcTime` at 5/30 minutes — the issue title is stable for the life
 *   of a lease; there is no reason to re-ask the hub for it on every poll tick.
 * - `refetchOnWindowFocus`/`refetchOnMount: false` — nothing about refocusing the
 *   tab or remounting a row (e.g. a `@for` track churn) should re-fire this read.
 *
 * One query per distinct `chunk_id`, deduped by TanStack's cache key — not batched.
 * The decisive property is isolation: one row's failing title can never blind
 * another row, or the list (mirrors the hub's own per-pointer degrade, D-084).
 *
 * The caller must never branch on `isError()`/`isPending()` here — read `data()`
 * optimistically and render whatever arrived, or nothing. `chunk_id` is what a row
 * *is*; the title is decoration that *arrived*.
 */
export function injectChunkTitleQuery(chunkId: () => string) {
  return injectQuery(() => {
    const id = chunkId();
    return {
      queryKey: runnerChunkPmItemsKey(id),
      enabled: !!id,
      queryFn: async (): Promise<runnerApi.PmItemsView> => {
        const { data, error } = await runnerApi.getPmItemsApiChunksChunkIdPmItemsGet({
          path: { chunk_id: id },
          throwOnError: false,
        });
        if (error) throw error;
        return data!;
      },
      refetchInterval: false as const,
      staleTime: 5 * 60_000,
      gcTime: 30 * 60_000,
      retry: false,
      refetchOnWindowFocus: false,
      refetchOnMount: false,
    };
  });
}
