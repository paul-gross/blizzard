import type { WorkItemsView } from 'fleet';

/** How many chunks one batch of the board's work-items read names. */
export const WORK_ITEMS_BATCH_SIZE = 100;

/** The board's chunk ids as read batches: each id once, sorted so the batches a board falls
 * into stay put as its lanes reshuffle, {@link WORK_ITEMS_BATCH_SIZE} to a batch. */
export function workItemBatches(chunkIds: readonly string[]): readonly (readonly string[])[] {
  const ids = [...new Set(chunkIds)].sort();
  const batches: string[][] = [];
  for (let start = 0; start < ids.length; start += WORK_ITEMS_BATCH_SIZE) {
    batches.push(ids.slice(start, start + WORK_ITEMS_BATCH_SIZE));
  }
  return batches;
}

/** Every answered batch's work items in one map by chunk id; a batch still reading adds nothing. */
export function mergeWorkItemBatches(
  batches: readonly (ReadonlyMap<string, WorkItemsView> | undefined)[],
): ReadonlyMap<string, WorkItemsView> {
  const merged = new Map<string, WorkItemsView>();
  for (const batch of batches) {
    for (const [chunkId, view] of batch ?? []) merged.set(chunkId, view);
  }
  return merged;
}

/** Whether a chunk's work items, last written at `updatedAt`, are still within the board's hold
 * at `now` — by the write's age alone, so a cache invalidation does not shorten it. */
export function withinHold(updatedAt: number, now: number, holdMs: number): boolean {
  return updatedAt > 0 && now - updatedAt < holdMs;
}
