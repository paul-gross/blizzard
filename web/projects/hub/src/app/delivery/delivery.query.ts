import type { Signal } from '@angular/core';
import type { QueryObserverResult } from '@tanstack/angular-query-experimental';
import { injectQueries } from '@tanstack/angular-query-experimental/inject-queries-experimental';

import { type FlowFactView, getFlowRecordApiFlowsFlowIdRecordGet, hubFlowsKey } from 'fleet';

/** How many of each flow's newest facts the page reads for its landing times and stages. */
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
        return [id, data!.facts] as const;
      },
    })),
    combine: (results) => {
      const read = results as unknown as readonly QueryObserverResult<readonly [string, readonly FlowFactView[]]>[];
      return { facts: new Map(read.flatMap((result) => (result.data ? [result.data] : []))), pending: read.some((result) => result.isPending) };
    },
  }));
}
