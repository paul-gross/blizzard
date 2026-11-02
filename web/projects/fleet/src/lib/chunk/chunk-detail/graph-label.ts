import type { ChunkDetail } from '../../api/hub';
import { compactRef } from '../../core/compact-ref';
import { formatUtcYmd } from '../../core/when';

/** A chunk's pinned graph as `G-XT6X#name-YYYYMMDD` — the compact ref alone when the graph
 * carries no name or creation date. */
export function chunkGraphLabel(detail: Pick<ChunkDetail, 'graph_id' | 'graph_name' | 'graph_created_at'>): string {
  const ref = compactRef(detail.graph_id);
  if (!detail.graph_name) return ref;
  const ymd = formatUtcYmd(detail.graph_created_at);
  if (!ymd) return ref;
  return `${ref}#${detail.graph_name}-${ymd}`;
}
