import { injectQuery } from '@tanstack/angular-query-experimental';

import {
  listProjectRunsApiProjectsProjectRunsGet,
  listRunsApiRunsGet,
  projectRunDeltaApiProjectsProjectRunsChunkIdGet,
  runDeltaApiRunsChunkIdGet,
  type RunDeltaView,
  type RunRowView,
} from 'fleet';
import type { ScopeSweep } from '../core/scope.domain';
import { laneStatus } from 'fleet/shell';
import { fromRunRowView } from '../core/scope.translate';
import { injectProjectFilter } from '../../core/project-filter';
import { lensedKey, hubRunDeltaKey, hubRunsKey } from '../../core/query-keys';

/**
 * The run list (`hub run list`) — every routine run minted in `[since, until)`, newest first: the lensed project's
 * (`GET /api/projects/{project}/runs`), or on All the tenant's (`GET /api/runs`), which only the gardening doorway
 * reads. `since` is a reactive accessor,
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
  return injectQuery(runsQueryOptions(since));
}

/** Run-list projection; the shared query cache retains the wire response. */
export function injectHubRunListQuery(since: () => string) {
  const read = runsQueryOptions(since);
  return injectQuery(() => ({ ...read(), select: (wire: RunRowView[]) => wire.map((row) => ({ ...row, laneStatus: laneStatus(row.outcome) })) }));
}

/** The run list as the {@link ScopeSweep}s of its scopes — the same read and cache entry as {@link injectHubRunsQuery}. */
export function injectHubScopeSweepsQuery(since: () => string) {
  const read = runsQueryOptions(since);
  return injectQuery(() => ({ ...read(), select: (wire: RunRowView[]): readonly ScopeSweep[] => wire.map(fromRunRowView) }));
}

function runsQueryOptions(since: () => string) {
  const project = injectProjectFilter();
  return () => {
    const lens = project();
    const window = { since: since() };
    return {
      queryKey: lensedKey(hubRunsKey(window.since), lens),
      queryFn: async (): Promise<RunRowView[]> => {
        const { data, error } =
          lens === null
            ? await listRunsApiRunsGet({ query: window, throwOnError: false })
            : await listProjectRunsApiProjectsProjectRunsGet({
                path: { project: lens },
                query: window,
                throwOnError: false,
              });
        if (error) throw error;
        return data ?? [];
      },
    };
  };
}

/**
 * One run's detail (`hub run get`) — `GET /api/projects/{project}/runs/{chunk_id}` under a lens, `GET
 * /api/runs/{chunk_id}` on All: its identity, derived outcome, and, per finding-set it
 * delivered, the added/observed/gone entries its own artifact published. Disabled
 * while `chunkId()` is `null` — `injectHubGraphQuery`'s own rest state for a route
 * param that can be absent.
 */
export function injectHubRunDeltaQuery(chunkId: () => string | null) {
  const project = injectProjectFilter();
  return injectQuery(() => {
    const id = chunkId();
    const lens = project();
    return {
      queryKey: lensedKey(hubRunDeltaKey(id), lens),
      enabled: id !== null,
      queryFn: async (): Promise<RunDeltaView> => {
        const { data, error } =
          lens === null
            ? await runDeltaApiRunsChunkIdGet({ path: { chunk_id: id! }, throwOnError: false })
            : await projectRunDeltaApiProjectsProjectRunsChunkIdGet({
                path: { project: lens, chunk_id: id! },
                throwOnError: false,
              });
        if (error) throw error;
        return data as RunDeltaView;
      },
    };
  });
}
