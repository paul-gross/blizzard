import type { Signal } from '@angular/core';
import { injectQuery, type QueryObserverResult } from '@tanstack/angular-query-experimental';
import { injectQueries } from '@tanstack/angular-query-experimental/inject-queries-experimental';

import { fromFlowFactView, getFlowRecordApiFlowsFlowIdRecordGet } from 'fleet';
import { LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet/shell';
import { boardChunksQueryOptions } from '../core/chunks.query';
import { activeFlowsQueryOptions } from '../core/flows.query';
import { injectProjectFilter } from '../core/project-filter';
import { hubFlowsKey } from '../core/query-keys';
import { laneRepositoriesQueryOptions } from '../core/repositories.query';
import type { DeliveryFlowFact } from './delivery.domain';
import { fromChunkSummary, fromFlowView, fromRepositorySummary } from './delivery.translate';

/** How many of each flow's newest facts the page reads for its landing times and stages. */
const HEAD_LIMIT = 200;

/** Each flow's facts, newest first. */
export interface FlowFacts {
  readonly facts: ReadonlyMap<string, readonly DeliveryFlowFact[]>;
  /** Whether any flow's facts are still unread. */
  readonly pending: boolean;
}

/** The tenant's active flows, as the delivery domain reads them, over the shared wire cache. */
export function injectDeliveryFlowsQuery() {
  return injectQuery(() => ({
    ...activeFlowsQueryOptions(),
    select: (flows: Parameters<typeof fromFlowView>[0][]) => flows.map(fromFlowView),
  }));
}

/** The tenant's repositories, as the delivery domain reads them, over the shared wire cache. */
export function injectDeliveryRepositoriesQuery() {
  return injectQuery(() => ({
    ...laneRepositoriesQueryOptions(),
    select: (repositories: Parameters<typeof fromRepositorySummary>[0][]) => repositories.map(fromRepositorySummary),
  }));
}

/** The board's chunk list under the project lens, as the delivery domain reads it, over the shared wire cache. */
export function injectDeliveryChunksQuery() {
  const projectFilter = injectProjectFilter();
  return injectQuery(() => {
    const options = boardChunksQueryOptions(projectFilter());
    return {
      ...options,
      refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
      select: (read: Parameters<typeof options.select>[0]) => read.chunks.map(fromChunkSummary),
    };
  });
}

/**
 * `GET /api/flows/{flow}/record?limit=200` for every flow id handed in — each flow's newest facts, one query per
 * flow, under the flows key so the flow events that refresh the board's record refresh these too.
 */
export function injectFlowFactsQuery(flowIds: () => readonly string[]): Signal<FlowFacts> {
  return injectQueries(() => ({
    queries: flowIds().map((id) => ({
      queryKey: [...hubFlowsKey, 'record-head', id] as const,
      queryFn: async () => {
        const { data, error } = await getFlowRecordApiFlowsFlowIdRecordGet({ path: { flow_id: id }, query: { limit: HEAD_LIMIT }, throwOnError: false });
        if (error) throw error;
        return [id, data!.facts] as const;
      },
      select: ([flowId, facts]: readonly [string, Parameters<typeof fromFlowFactView>[0][]]) => [flowId, facts.map(fromFlowFactView)] as const,
    })),
    combine: (results) => {
      const read = results as unknown as readonly QueryObserverResult<readonly [string, readonly DeliveryFlowFact[]]>[];
      return { facts: new Map(read.flatMap((result) => (result.data ? [result.data] : []))), pending: read.some((result) => result.isPending) };
    },
  }));
}
