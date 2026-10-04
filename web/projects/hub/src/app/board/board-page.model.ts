import { type ChunkSummary } from 'fleet';

/** A requested lane placement — `chunkId` lands immediately after `afterChunkId`, or at the very top when that is `null`. */
export interface LaneMove {
  readonly chunkId: string;
  readonly afterChunkId: string | null;
}

/** Every chunk id with an open decision, as a set each board card can test its own `gate` flag against. */
export function gatedChunkIds(decisions: readonly { readonly chunk_id: string }[]): ReadonlySet<string> {
  return new Set(decisions.map((decision) => decision.chunk_id));
}

/** A hub-ordered list's entries as bare chunk ids, keeping the hub's order. */
export function orderedChunkIds(entries: readonly { readonly chunk_id: string }[]): readonly string[] {
  return entries.map((entry) => entry.chunk_id);
}

/**
 * `chunks`, with each pending-promote chunk's status overridden to `'ready'` and each pending-delete chunk dropped.
 * Returns `chunks` itself when neither override applies.
 */
export function withPendingBoardChanges(
  chunks: readonly ChunkSummary[],
  pendingPromotes: readonly { readonly chunkId: string }[],
  pendingDeletes: readonly { readonly chunkId: string }[],
): readonly ChunkSummary[] {
  const deletingIds = new Set(pendingDeletes.map((vars) => vars.chunkId));
  const visible = deletingIds.size === 0 ? chunks : chunks.filter((c) => !deletingIds.has(c.chunk_id));
  if (pendingPromotes.length === 0) return visible;
  const pendingIds = new Set(pendingPromotes.map((vars) => vars.chunkId));
  return visible.map((chunk) => (pendingIds.has(chunk.chunk_id) ? { ...chunk, status: 'ready' } : chunk));
}

/**
 * A pending reposition's requested placement, replayed over a copy of `order` — `move.chunkId`
 * lands immediately after `move.afterChunkId`, or at the very top when that is `null`, mirroring
 * the anchor semantics `BoardColumn.dropped` computes when it emits a `BoardReposition`. `order`
 * itself is left untouched.
 */
function withRequestedPosition(order: readonly string[], move: LaneMove): string[] {
  const withoutMoved = order.filter((id) => id !== move.chunkId);
  const afterIndex = move.afterChunkId === null ? -1 : withoutMoved.indexOf(move.afterChunkId);
  withoutMoved.splice(afterIndex + 1, 0, move.chunkId);
  return withoutMoved;
}

/** `order` with every pending move's requested placement folded in, in the order the moves were issued. */
export function foldRepositions(order: readonly string[], moves: readonly LaneMove[]): readonly string[] {
  let folded = order;
  for (const move of moves) {
    folded = withRequestedPosition(folded, move);
  }
  return folded;
}

/**
 * The URL-selected chunk id held to a chunk that exists — one in `chunks`, or the one whose own detail read
 * resolved (`linkedDetailChunkId`) — else `null`.
 */
export function resolveSelectedChunk(
  chunkId: string | null,
  chunks: readonly ChunkSummary[],
  linkedDetailChunkId: string | null | undefined,
): string | null {
  if (chunkId === null) return null;
  if (chunks.some((chunk) => chunk.chunk_id === chunkId)) return chunkId;
  return linkedDetailChunkId === chunkId ? chunkId : null;
}
