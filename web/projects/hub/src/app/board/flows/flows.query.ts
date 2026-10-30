import { injectInfiniteQuery, injectQuery } from '@tanstack/angular-query-experimental';

import {
  type FlowRecordPage,
  type FlowView,
  getFlowApiFlowsFlowIdGet,
  getFlowRecordApiFlowsFlowIdRecordGet,
  hubFlowsKey,
  listFlowsApiFlowsGet,
} from 'fleet';
import { LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet/shell';

/** The page size the flow record asks for. */
const RECORD_PAGE = 50;

/**
 * `GET /api/flows?active=true` — the tenant's active flows, each with its line. Tenant-wide and never lensed, so it
 * sends no `?project=`: a branch's line is shared by every project landing on it, and a lensed chunk still shows whom
 * it waits behind. Freshness: `EVENT_INVALIDATION_REGISTRY` (`web/projects/fleet/src/lib/sse/fleet-live.ts`).
 */
export function injectActiveFlowsQuery() {
  return injectQuery(() => ({
    queryKey: [...hubFlowsKey, 'active'] as const,
    queryFn: async (): Promise<FlowView[]> => {
      const { data, error } = await listFlowsApiFlowsGet({ query: { active: true }, throwOnError: false });
      if (error) throw error;
      return data?.flows ?? [];
    },
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}

/** `GET /api/flows/{flow}` — one flow, whatever its activity; disabled while `flowId` is `null`. */
export function injectFlowQuery(flowId: () => string | null) {
  return injectQuery(() => {
    const id = flowId();
    return {
      queryKey: [...hubFlowsKey, 'flow', id] as const,
      enabled: id !== null,
      queryFn: async (): Promise<FlowView> => {
        const { data, error } = await getFlowApiFlowsFlowIdGet({ path: { flow_id: id ?? '' }, throwOnError: false });
        if (error || !data) throw error ?? new Error(`no flow ${id}`);
        return data;
      },
    };
  });
}

/**
 * `GET /api/flows/{flow}/record` — the flow's facts, newest first, a page at a time: each next page asks for the facts
 * `before` the previous page's `next_before`, and a page with none is the last. Disabled while `flowId` is `null`.
 */
export function injectFlowRecordQuery(flowId: () => string | null) {
  return injectInfiniteQuery(() => {
    const id = flowId();
    return {
      queryKey: [...hubFlowsKey, 'record', id] as const,
      enabled: id !== null,
      initialPageParam: undefined as number | undefined,
      queryFn: async ({ pageParam }: { pageParam: number | undefined }): Promise<FlowRecordPage> => {
        const { data, error } = await getFlowRecordApiFlowsFlowIdRecordGet({
          path: { flow_id: id ?? '' },
          query: { before: pageParam, limit: RECORD_PAGE },
          throwOnError: false,
        });
        if (error) throw error;
        return data ?? { facts: [], next_before: null };
      },
      getNextPageParam: (last: FlowRecordPage) => last.next_before ?? undefined,
    };
  });
}
