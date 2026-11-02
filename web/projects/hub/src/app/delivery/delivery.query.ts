import type { Signal } from '@angular/core';
import type { QueryObserverResult } from '@tanstack/angular-query-experimental';
import { injectQueries } from '@tanstack/angular-query-experimental/inject-queries-experimental';

import { type ChunkDetail, chunkDetailKey, type FlowFactView, getFlowRecordApiFlowsFlowIdRecordGet, hubApi, hubFlowsKey } from 'fleet';

/** How many of each flow's newest facts the page reads for its bounces and landing times. */
const HEAD_LIMIT = 200;

/** Each flow's facts, newest first. */
export interface FlowFacts {
  readonly facts: ReadonlyMap<string, readonly FlowFactView[]>;
  /** Whether any flow's facts are still unread. */
  readonly pending: boolean;
}

/**
 * `GET /api/flows/{flow}/record?limit=200` for every flow id handed in — each flow's newest facts, one query per
 * flow, under the flows key so the flow events that refresh the board's record refresh these too.
 */
export function injectFlowFactsQuery(flowIds: () => readonly string[]): Signal<FlowFacts> {
  return injectQueries(() => ({
    queries: flowIds().map((id) => ({
      queryKey: [...hubFlowsKey, 'record-head', id] as const,
      queryFn: async (): Promise<readonly [string, readonly FlowFactView[]]> => {
        const { data, error } = await getFlowRecordApiFlowsFlowIdRecordGet({ path: { flow_id: id }, query: { limit: HEAD_LIMIT }, throwOnError: false });
        if (error) throw error;
        return [id, data?.facts ?? []] as const;
      },
    })),
    combine: (results) => {
      const read = results as unknown as readonly QueryObserverResult<readonly [string, readonly FlowFactView[]]>[];
      return { facts: new Map(read.flatMap((result) => (result.data ? [result.data] : []))), pending: read.some((result) => result.isPending) };
    },
  }));
}

/**
 * `GET /api/chunks/{chunk}` for every chunk id handed in, under the board dock's own chunk-detail key so a chunk read
 * here and there is read once — answering each chunk's bounce record by chunk id. A chunk not yet read is absent.
 */
export function injectChunkBouncesQuery(chunkIds: () => readonly string[]): Signal<ReadonlyMap<string, readonly hubApi.BounceView[]>> {
  return injectQueries(() => ({
    queries: chunkIds().map((id) => ({
      queryKey: chunkDetailKey('hub', id),
      queryFn: async (): Promise<ChunkDetail> => {
        const { data, error } = await hubApi.getChunkApiChunksChunkIdGet({ path: { chunk_id: id }, throwOnError: false });
        if (error || !data) throw error ?? new Error(`no chunk ${id}`);
        return data;
      },
    })),
    combine: (results) =>
      new Map(
        (results as unknown as readonly QueryObserverResult<ChunkDetail>[]).flatMap((result) => (result.data ? [[result.data.chunk_id, result.data.bounces ?? []] as const] : [])),
      ),
  }));
}
