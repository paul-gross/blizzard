import type { Urgency } from 'fleet/core';
import type { ChunkUrgencyVars } from './urgency.mutations';

/** Show each chunk's most recently requested urgency while its write settles. */
export function withPendingUrgencies<T extends { readonly chunk_id: string; readonly urgency: { readonly id: string; readonly slug: string } | null }>(chunks: readonly T[], pending: readonly ChunkUrgencyVars[], urgencies: readonly Urgency[]): readonly T[] {
  const byChunk = new Map(pending.map((v) => [v.chunkId, v.urgencyId]));
  const byId = new Map(urgencies.map((u) => [u.id, u]));
  return chunks.map((chunk) => {
    const urgency = byId.get(byChunk.get(chunk.chunk_id) ?? '');
    return urgency ? { ...chunk, urgency: { id: urgency.id, slug: urgency.slug } } : chunk;
  });
}
