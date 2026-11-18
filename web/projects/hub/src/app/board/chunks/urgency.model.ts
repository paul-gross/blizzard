import type { ChunkSummary, Urgency } from 'fleet';
import type { ChunkUrgencyVars } from './urgency.mutations';

/** Show each chunk's most recently requested urgency while its write settles. */
export function withPendingUrgencies(chunks: readonly ChunkSummary[], pending: readonly ChunkUrgencyVars[], urgencies: readonly Urgency[]): readonly ChunkSummary[] {
  const byChunk = new Map(pending.map((v) => [v.chunkId, v.urgencyId]));
  const byId = new Map(urgencies.map((u) => [u.id, u]));
  return chunks.map((chunk) => {
    const urgency = byId.get(byChunk.get(chunk.chunk_id) ?? '');
    return urgency ? { ...chunk, urgency: { urgency_id: urgency.id, slug: urgency.slug } } : chunk;
  });
}
