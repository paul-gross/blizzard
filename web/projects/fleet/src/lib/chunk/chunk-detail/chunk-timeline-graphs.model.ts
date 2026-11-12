import type { ChunkDetail } from '../../api/hub';
import type { HistoryRow } from './chunk-timeline-rows';

type GraphRow = Pick<HistoryRow, 'graphId' | 'graphName'>;

export interface TimelineGraphHeader {
  readonly id: string | null;
  readonly label: string;
}

/** Graphless events neither acquire a graph nor end a consecutive graph run. */
export function deriveGraphHeaders(rows: readonly GraphRow[]): readonly (TimelineGraphHeader | null)[] {
  let previous: string | null = null;
  return rows.map((row) => {
    const key = row.graphId ?? row.graphName;
    if (key === null) return null;
    const header = key !== previous ? { id: row.graphId, label: row.graphName ?? key } : null;
    previous = key;
    return header;
  });
}

export function deriveActiveGraphHeader(
  rows: readonly GraphRow[],
  detail: Pick<ChunkDetail, 'graph_id' | 'graph_name'>,
): TimelineGraphHeader | null {
  const last = rows.filter((row) => row.graphId !== null || row.graphName !== null).at(-1);
  return !last || (last.graphId ?? last.graphName) !== detail.graph_id
    ? { id: detail.graph_id, label: detail.graph_name ?? detail.graph_id }
    : null;
}
