import type { Signal } from '@angular/core';
import { injectQuery, type QueryObserverResult } from '@tanstack/angular-query-experimental';
import { injectQueries } from '@tanstack/angular-query-experimental/inject-queries-experimental';

import { getGraphApiGraphsGraphIdGet, listGraphsApiGraphsGet, type GraphSummaryView, type GraphView, hubGraphKey, hubGraphsKey } from 'fleet';

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
    return { ...graphQueryOptions(id), enabled: id !== null };
  });
}

/** One graph's read options — the key and fetch {@link injectHubGraphQuery} and {@link injectHubGraphNamesQuery}
 * share, so both read one cache entry per graph. */
function graphQueryOptions(id: string | null) {
  return {
    queryKey: hubGraphKey(id),
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
}

/** Each graph's name by its id, over the graph reads of every id handed in — a graph not yet read, or unreadable,
 * is absent. */
export function injectHubGraphNamesQuery(graphIds: () => readonly string[]): Signal<ReadonlyMap<string, string>> {
  return injectQueries(() => ({
    queries: [...new Set(graphIds())].map((id) => graphQueryOptions(id)),
    // `combine` is handed the observers' plain results; the experimental typing names them signal proxies.
    combine: (results) =>
      new Map(
        (results as unknown as readonly QueryObserverResult<GraphView>[])
          .flatMap((result) => (result.data ? [[result.data.graph_id, result.data.name] as const] : [])),
      ),
  }));
}
