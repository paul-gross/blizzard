import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  getGraphApiGraphsGraphIdGet,
  listGraphsApiGraphsGet,
  type GraphSummaryView,
  type GraphView,
} from '../api/hub';
import { hubGraphKey, hubGraphsKey } from '../query-keys';

/**
 * Hub `GET /api/graphs` read — every minted graph's summary row (name, `graph_id`,
 * `created_at`, `entry_node_id`, and the derived `effective` marker), through
 * TanStack Query and the generated hub client (bzh:generated-client). Graphs are
 * immutable and minted rarely — not in the SSE event vocabulary
 * (`HUB_EVENT_TYPES` in `fleet-live.ts`) — so a plain query with no
 * `refetchInterval` is correct; nothing live-invalidates this list.
 */
export function injectHubGraphsQuery() {
  return injectQuery(() => ({
    queryKey: hubGraphsKey,
    queryFn: async (): Promise<GraphSummaryView[]> => {
      const { data, error } = await listGraphsApiGraphsGet({ throwOnError: false });
      if (error) throw error;
      return data ?? [];
    },
  }));
}

/** An error the graph-detail queryFn throws, carrying the HTTP status the fetch
 * actually returned (the generated `error` value doesn't — it's just the parsed
 * error body) so {@link shouldRetryGraphFetch} can special-case a 404. */
export class GraphFetchError extends Error {
  constructor(readonly status: number) {
    super(`graph fetch failed with status ${status}`);
  }
}

/** The graph-detail query's `retry` predicate, exported standalone so it's
 * unit-testable without driving TanStack Query's real retry/backoff machinery. A
 * 404 (unknown graph id) is terminal — no retry — matching every other failure's
 * default cap of 3. */
export function shouldRetryGraphFetch(failureCount: number, error: Error): boolean {
  return !(error instanceof GraphFetchError && error.status === 404) && failureCount < 3;
}

/**
 * Hub `GET /api/graphs/{graph_id}` read — one minted graph's full immutable
 * structure (nodes, edges, choices). Reactive over the selected graph id: pass an
 * accessor (a signal getter) — the query re-keys and re-fetches as the route param
 * changes, and disables itself while nothing is selected.
 *
 * A 404 (unknown graph id) is not retried — it's a terminal answer, not a
 * transient failure — so `graph-detail.ts`'s error state surfaces immediately
 * instead of after TanStack Query's default 3 retries (~7-10s of spinner on a bad
 * deep link before this fix).
 */
export function injectHubGraphQuery(graphId: () => string | null) {
  return injectQuery(() => {
    const id = graphId();
    return {
      queryKey: hubGraphKey(id),
      enabled: id !== null,
      queryFn: async (): Promise<GraphView> => {
        const { data, error, response } = await getGraphApiGraphsGraphIdGet({
          path: { graph_id: id! },
          throwOnError: false,
        });
        if (error) throw new GraphFetchError(response?.status ?? 0);
        return data!;
      },
      retry: shouldRetryGraphFetch,
    };
  });
}
