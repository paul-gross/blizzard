import { compactRef } from '../../core/compact-ref';
import { formatUtcYmd } from '../../core/when';

export interface ChunkGraphIdentity {
  readonly graph_id: string;
  readonly graph_name: string | null;
  readonly graph_created_at: string | null;
}

/** A chunk's pinned graph as `G-XT6X#name-YYYYMMDD` — the compact ref alone when the graph
 * carries no name or creation date. */
export function chunkGraphLabel(detail: ChunkGraphIdentity): string {
  const ref = compactRef(detail.graph_id);
  if (!detail.graph_name) return ref;
  const ymd = formatUtcYmd(detail.graph_created_at);
  if (!ymd) return ref;
  return `${ref}#${detail.graph_name}-${ymd}`;
}
