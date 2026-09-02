import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  listRunsApiRunsGet,
  runDeltaApiRunsChunkIdGet,
  type RunDeltaView,
  type RunRowView,
} from '../api/hub';
import { hubRunDeltaKey, hubRunsKey } from '../query-keys';

/**
 * Hub `GET /api/runs` read (blizzard#401 Phase 3, `hub run list`) — every routine run
 * minted in `[since, until)`, newest first. `since` is a reactive accessor,
 * `injectHubRoutineTrendQuery`'s own shape, so a caller can recompute the window
 * without re-wiring the query; the window's lower edge rides the key (`hubRunsKey`),
 * so a new `since` is its own cache entry.
 *
 * `until` is never sent: the hub's own `_RunWindow.of` already defaults an omitted
 * `until` to *its* clock's `now()`, and a browser `Date.now()` sent as `until` risks
 * excluding a run the hub minted between the two clocks if the hub's is ever ahead —
 * this surface exists specifically to show the newest runs, so it defers to the one
 * clock actually recording `minted_at`.
 */
export function injectHubRunsQuery(since: () => string) {
  return injectQuery(() => ({
    queryKey: hubRunsKey(since()),
    queryFn: async (): Promise<RunRowView[]> => {
      const { data, error } = await listRunsApiRunsGet({
        query: { since: since() },
        throwOnError: false,
      });
      if (error) throw error;
      return data ?? [];
    },
  }));
}

/**
 * Hub `GET /api/runs/{chunk_id}` read (blizzard#401 Phase 3, `hub run show`) — one
 * run's full detail: its identity, derived outcome, and, per finding-set it
 * delivered, the added/observed/gone entries its own artifact published. Disabled
 * while `chunkId()` is `null` — `injectHubGraphQuery`'s own rest state for a route
 * param that can be absent.
 */
export function injectHubRunDeltaQuery(chunkId: () => string | null) {
  return injectQuery(() => {
    const id = chunkId();
    return {
      queryKey: hubRunDeltaKey(id),
      enabled: id !== null,
      queryFn: async (): Promise<RunDeltaView> => {
        const { data, error } = await runDeltaApiRunsChunkIdGet({
          path: { chunk_id: id! },
          throwOnError: false,
        });
        if (error) throw error;
        return data as RunDeltaView;
      },
    };
  });
}
