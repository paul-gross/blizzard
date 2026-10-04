import { deriveWorkItemsState, type ChunkDetail, type ChunkStatus, type WorkItemsQuery, type WorkItemsState } from 'fleet';

/**
 * The status `detail` will read once a pending Pause or Complete on it settles, when that outcome is total
 * over the rendered status (`bzh:frontend-pending-override`) — `null` while nothing overrides `detail.status`.
 * Complete always predicts `done` and wins over a pending Pause; Pause predicts `paused` only where the hub's
 * `status_if_paused` settles it there — never where the hub refuses the pause or a human gate outranks it;
 * Resume (`paused: false`) and every other chunk's mutation predict nothing.
 */
export function pendingStatusOverride(
  detail: ChunkDetail | undefined,
  pendingCompletes: readonly { readonly chunkId: string }[],
  pendingPauses: readonly { readonly chunkId: string; readonly paused: boolean }[],
): ChunkStatus | null {
  if (detail === undefined) return null;
  if (pendingCompletes.some((vars) => vars.chunkId === detail.chunk_id)) return 'done';
  const pausing = pendingPauses.some((vars) => vars.chunkId === detail.chunk_id && vars.paused);
  if (pausing && detail.status_if_paused === 'paused') return 'paused';
  return null;
}

/** The ids of the questions an answer mutation is in flight for. */
export function pendingQuestionIds(pendingAnswers: readonly { readonly questionId: string }[]): readonly string[] {
  return pendingAnswers.map((vars) => vars.questionId);
}

/** The open chunk's aggregate — `undefined` while the dock is closed, whatever a disabled query still holds. */
export function openDetail(chunkId: string | null, data: ChunkDetail | undefined): ChunkDetail | undefined {
  return chunkId === null ? undefined : data;
}

/** The open chunk's work-items pane state — `loading` with no items while the dock is closed, since the
 * disabled query's own triad would otherwise be read for a chunk nobody opened. */
export function openWorkItems(chunkId: string | null, query: WorkItemsQuery): WorkItemsState {
  if (chunkId === null) return { status: 'loading', items: [] };
  return deriveWorkItemsState(query);
}
