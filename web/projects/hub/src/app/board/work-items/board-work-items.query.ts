import { inject, type Signal } from '@angular/core';
import { keepPreviousData, QueryClient, type QueryObserverResult } from '@tanstack/angular-query-experimental';
import { injectQueries } from '@tanstack/angular-query-experimental/inject-queries-experimental';

import { FLEET_CLOCK, getWorkItemsApiChunksChunkIdWorkItemsGet, hubChunkWorkItemsKey, hubWorkItemsBatchKey, type WorkItemsView } from 'fleet';
import { mergeWorkItemBatches, withinHold, workItemBatches } from './board-work-items.model';

/** How many single-chunk reads one batch runs at once. */
export const WORK_ITEMS_READ_CONCURRENCY = 6;

/** How long the board reuses a chunk's work items: titles rarely move, and each read can reach
 * an external forge. */
const WORK_ITEMS_HOLD_MS = 5 * 60_000;

/** What a chunk whose read failed answers with: no items, so the board stops waiting on it. */
const NO_ITEMS_READ: WorkItemsView = { items: [] };

/** One chunk's work items through the generated hub client, read into the dock's own entry —
 * `null` when the read fails. */
async function readChunkWorkItems(queryClient: QueryClient, chunkId: string): Promise<WorkItemsView | null> {
  try {
    return await queryClient.fetchQuery({
      queryKey: hubChunkWorkItemsKey(chunkId),
      queryFn: async ({ signal }) => {
        const { data, error } = await getWorkItemsApiChunksChunkIdWorkItemsGet({
          path: { chunk_id: chunkId },
          signal,
          throwOnError: false,
        });
        if (error || !data) throw error ?? new Error(`no work items for ${chunkId}`);
        return data;
      },
      // The hold below has already judged this entry stale.
      staleTime: 0,
    });
  } catch {
    return null;
  }
}

/**
 * One batch of chunks' work items, keyed by chunk id. A chunk whose items were written within the
 * hold — by this read or by the chunk dock's own — is answered from that entry, whatever has
 * invalidated it since, so a live update or a reconnect re-reads nothing from the forge. The rest
 * are read one chunk at a time, a few at once, into the dock's entry. A chunk whose read fails
 * answers as carrying no items — its card stops waiting and names its work refs alone until a
 * read past the hold succeeds — and an aborted batch stops starting reads.
 *
 * The hub has no batched read: the unheld chunks are where a bulk endpoint would take over.
 */
async function readWorkItemsBatch(
  queryClient: QueryClient,
  now: number,
  chunkIds: readonly string[],
  signal: AbortSignal,
): Promise<ReadonlyMap<string, WorkItemsView>> {
  const found = new Map<string, WorkItemsView>();
  const unheld: string[] = [];
  for (const chunkId of chunkIds) {
    const state = queryClient.getQueryState<WorkItemsView>(hubChunkWorkItemsKey(chunkId));
    if (state?.data !== undefined && withinHold(state.dataUpdatedAt, now, WORK_ITEMS_HOLD_MS)) {
      found.set(chunkId, state.data);
    } else {
      unheld.push(chunkId);
    }
  }
  let next = 0;
  const worker = async (): Promise<void> => {
    while (next < unheld.length && !signal.aborted) {
      const chunkId = unheld[next++];
      found.set(chunkId, (await readChunkWorkItems(queryClient, chunkId)) ?? NO_ITEMS_READ);
    }
  };
  await Promise.all(Array.from({ length: Math.min(WORK_ITEMS_READ_CONCURRENCY, unheld.length) }, worker));
  return found;
}

/**
 * The board's second stage: every chunk's work items, read after the chunk list has answered
 * so the lanes never wait on a work source. The ids go out in batches
 * (`board-work-items.model.ts`), each its own query, so a batch's titles land as soon as it
 * answers. A batch the board's chunks reshuffle into holds its previous answer while it re-reads.
 */
export function injectBoardWorkItemsQuery(chunkIds: () => readonly string[]): Signal<ReadonlyMap<string, WorkItemsView>> {
  const queryClient = inject(QueryClient);
  const clock = inject(FLEET_CLOCK);
  return injectQueries(() => ({
    queries: workItemBatches(chunkIds()).map((batch) => ({
      queryKey: hubWorkItemsBatchKey(batch),
      queryFn: ({ signal }: { signal: AbortSignal }) => readWorkItemsBatch(queryClient, clock(), batch, signal),
      staleTime: WORK_ITEMS_HOLD_MS,
      refetchOnWindowFocus: false,
      placeholderData: keepPreviousData,
    })),
    // `combine` is handed the observers' plain results; the experimental typing names them
    // signal proxies, so the results are read as what they are.
    combine: (results) =>
      mergeWorkItemBatches(
        (results as unknown as readonly QueryObserverResult<ReadonlyMap<string, WorkItemsView>>[]).map((result) => result.data),
      ),
  }));
}
