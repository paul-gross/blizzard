import { injectQuery } from '@tanstack/angular-query-experimental';
import { runnerApi } from 'fleet';

import { runnerChunkWorkItemsDetailKey, runnerChunkWorkItemsKey } from './query-keys';

/** The `GET /api/chunks/{chunk_id}/work-items` fetch both queries below share
 * — {@link injectChunkTitleQuery} and {@link injectChunkWorkItemsDetailQuery}
 * hit the identical endpoint and differ only in cache key and observer
 * options (the real severability distinction between them), never in how the
 * response is fetched. */
async function fetchWorkItems(chunkId: string): Promise<runnerApi.WorkItemsView> {
  const { data, error } = await runnerApi.getWorkItemsApiChunksChunkIdWorkItemsGet({
    path: { chunk_id: chunkId },
    throwOnError: false,
  });
  if (error) throw error;
  return data!;
}

/**
 * Runner `GET /api/chunks/{chunk_id}/work-items` read — the layered pass-through
 * (panel → its own runner → hub → vendor, with the hub's credentials) that
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
 * another row, or the list (mirrors the hub's own per-pointer degrade).
 *
 * The caller must never branch on `isError()`/`isPending()` here — read `data()`
 * optimistically and render whatever arrived, or nothing. `chunk_id` is what a row
 * *is*; the title is decoration that *arrived*.
 */
export function injectChunkTitleQuery(chunkId: () => string) {
  return injectQuery(() => {
    const id = chunkId();
    return {
      queryKey: runnerChunkWorkItemsKey(id),
      enabled: !!id,
      queryFn: () => fetchWorkItems(id),
      refetchInterval: false as const,
      staleTime: 5 * 60_000,
      gcTime: 30 * 60_000,
      retry: false,
      refetchOnWindowFocus: false,
      refetchOnMount: false,
    };
  });
}

/**
 * Runner `GET /api/chunks/{chunk_id}/work-items` read — same endpoint as
 * {@link injectChunkTitleQuery}, but for the chunk detail route's Issues
 * section (issue #318), which renders a real loading/error/empty triad
 * ({@link ChunkIssuePane}'s `WorkItemsState`) rather than decoration a row
 * can silently drop. Mirrors `fleet`'s own `injectHubChunkWorkItemsQuery`
 * (`chunk-work-items.query.ts`) — default retry, a short 30s `staleTime`,
 * no polling — the config a caller is expected to branch `isError()`/
 * `isPending()` against, unlike the severable read above. Its own key
 * ({@link runnerChunkWorkItemsDetailKey}) so this query never shares a
 * cache entry or observer options with the row-decoration read. `enabled: id
 * !== null` mirrors {@link injectChunkDetailQuery}'s own sentinel — an empty
 * string is a real (if pathological) chunk id here, not "no id yet".
 */
export function injectChunkWorkItemsDetailQuery(chunkId: () => string | null) {
  return injectQuery(() => {
    const id = chunkId();
    return {
      queryKey: runnerChunkWorkItemsDetailKey(id ?? ''),
      enabled: id !== null,
      queryFn: () => fetchWorkItems(id!),
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    };
  });
}
