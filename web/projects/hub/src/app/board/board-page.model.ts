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
 * `chunks`, with each pending-promote chunk's status overridden to `'ready'`, each pending-demote `ready` chunk's to
 * `'not_ready'` (each predicting the `promotable` that status reads after the verb), and each pending-delete chunk dropped. Only a `ready` chunk predicts a demote: any other status
 * (a paused chunk in particular) stays as read, since the hub's verdict on it is not known from here.
 * Returns `chunks` itself when no override applies.
 */
export function withPendingBoardChanges(
  chunks: readonly ChunkSummary[],
  pendingPromotes: readonly { readonly chunkId: string }[],
  pendingDeletes: readonly { readonly chunkId: string }[],
  pendingDemotes: readonly { readonly chunkId: string }[] = [],
): readonly ChunkSummary[] {
  const deletingIds = new Set(pendingDeletes.map((vars) => vars.chunkId));
  const visible = deletingIds.size === 0 ? chunks : chunks.filter((c) => !deletingIds.has(c.chunk_id));
  if (pendingPromotes.length === 0 && pendingDemotes.length === 0) return visible;
  const promotingIds = new Set(pendingPromotes.map((vars) => vars.chunkId));
  const demotingIds = new Set(pendingDemotes.map((vars) => vars.chunkId));
  return visible.map((chunk) => {
    if (promotingIds.has(chunk.chunk_id)) return { ...chunk, status: 'ready', promotable: false };
    if (demotingIds.has(chunk.chunk_id) && chunk.status === 'ready') {
      return { ...chunk, status: 'not_ready', promotable: true };
    }
    return chunk;
  });
}

/** The pending lane crossings that name a placement — an absent anchor is the tail, which an unranked card already takes. */
export function placedMoves(
  pending: readonly { readonly chunkId: string; readonly afterChunkId?: string | null }[],
): readonly LaneMove[] {
  return pending.flatMap((vars) =>
    vars.afterChunkId === undefined ? [] : [{ chunkId: vars.chunkId, afterChunkId: vars.afterChunkId }],
  );
}

/**
 * A pending reposition's requested placement, replayed over a copy of `order` per {@link LaneMove}.
 * `order` itself is left untouched.
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
