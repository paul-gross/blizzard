import { inject, type Signal } from '@angular/core';
import { keepPreviousData, QueryClient, type QueryObserverResult } from '@tanstack/angular-query-experimental';
import { injectQueries } from '@tanstack/angular-query-experimental/inject-queries-experimental';

import {
  FLEET_CLOCK,
  getBulkWorkItemsApiWorkItemsGet,
  hubBoardChunkWorkItemsKey,
  hubChunkWorkItemsKey,
  hubWorkItemsBatchKey,
  type ChunkWorkItemsView,
  type WorkItemsView,
} from 'fleet';
import { mergeWorkItemBatches, withinHold, workItemBatches } from './board-work-items.model';

/** How long the board reuses a chunk's work items: titles rarely move, and each read can reach
 * an external forge. */
const WORK_ITEMS_HOLD_MS = 5 * 60_000;

/** What a chunk the board could not read answers with: no items, so the board stops waiting on it. */
const NO_ITEMS_READ: WorkItemsView = { items: [] };

/** A chunk's held work items: the dock's entry, a superset since it carries comments, else the board's own. */
function heldWorkItems(queryClient: QueryClient, now: number, chunkId: string): WorkItemsView | undefined {
  for (const key of [hubChunkWorkItemsKey(chunkId), hubBoardChunkWorkItemsKey(chunkId)]) {
    const state = queryClient.getQueryState<WorkItemsView>(key);
    if (state?.data !== undefined && withinHold(state.dataUpdatedAt, now, WORK_ITEMS_HOLD_MS)) return state.data;
  }
  return undefined;
}

/**
 * One batch of chunks' work items, keyed by chunk id. A chunk whose items were written within the
 * hold — by this read or by the chunk dock's own — is answered from that entry, whatever has
 * invalidated it since, so a live update or a reconnect re-reads nothing from the forge. The rest
 * go out in one bulk request, and each chunk it answers is held under the board's own key: the
 * dock's entry carries comments a bulk answer does not, so the board never writes it. A chunk the
 * answer omits, or every unheld chunk when the request fails, answers as carrying no items — its
 * card stops waiting and names its work refs alone — and is not held, so the next read retries it.
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
    const held = heldWorkItems(queryClient, now, chunkId);
    if (held !== undefined) found.set(chunkId, held);
    else unheld.push(chunkId);
  }
  if (unheld.length === 0 || signal.aborted) return found;
  let data: ChunkWorkItemsView | undefined;
  try {
    data = (
      await getBulkWorkItemsApiWorkItemsGet({
        query: { chunk_id: unheld },
        signal,
        throwOnError: false,
      })
    ).data;
  } catch {
    data = undefined;
  }
  for (const chunkId of unheld) {
    const view = data?.chunks[chunkId];
    if (view === undefined) {
      found.set(chunkId, NO_ITEMS_READ);
      continue;
    }
    const items: WorkItemsView = { items: view };
    // The hold is written by a fetch that already has its answer, not by a direct cache write.
    await queryClient.fetchQuery({ queryKey: hubBoardChunkWorkItemsKey(chunkId), queryFn: () => items, staleTime: 0 });
    found.set(chunkId, items);
  }
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
