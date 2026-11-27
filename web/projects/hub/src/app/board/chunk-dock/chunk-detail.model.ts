import { deriveWorkItemsState, type WorkItemsQuery, type WorkItemsState } from 'fleet/chunk-detail';
import { type ChunkDetailAggregate } from 'fleet/chunk-page';
import { isPendingFor } from 'fleet/core';

/**
 * The status `detail` will read once a pending Pause or Complete on it settles, when that outcome is total
 * over the rendered status (`bzh:frontend-pending-override`) — `null` while nothing overrides `detail.status`.
 * Complete always predicts `done` and wins over a pending Pause; Pause predicts `paused` only where the hub's
 * `status_if_paused` settles it there — never where the hub refuses the pause or a human gate outranks it;
 * Resume (`paused: false`) and every other chunk's mutation predict nothing.
 */
export function pendingStatusOverride(
  detail: Pick<ChunkDetailAggregate, 'chunk_id' | 'status_if_paused'> | undefined,
  pendingCompletes: readonly { readonly chunkId: string }[],
  pendingPauses: readonly { readonly chunkId: string; readonly paused: boolean }[],
): 'done' | 'paused' | null {
  if (detail === undefined) return null;
  if (isPendingFor(pendingCompletes, (vars) => vars.chunkId === detail.chunk_id)) return 'done';
  const pausing = isPendingFor(pendingPauses, (vars) => vars.chunkId === detail.chunk_id && vars.paused);
  if (pausing && detail.status_if_paused?.kind === 'paused') return 'paused';
  return null;
}


/** The open chunk's aggregate — `undefined` while the dock is closed, whatever a disabled query still holds. */
export function openDetail<T>(chunkId: string | null, data: T | undefined): T | undefined {
  return chunkId === null ? undefined : data;
}

/** The open chunk's work-items pane state — `loading` with no items while the dock is closed, since the
 * disabled query's own triad would otherwise be read for a chunk nobody opened. */
export function openWorkItems(chunkId: string | null, query: WorkItemsQuery): WorkItemsState {
  if (chunkId === null) return { status: 'loading', items: [] };
  return deriveWorkItemsState(query);
}
