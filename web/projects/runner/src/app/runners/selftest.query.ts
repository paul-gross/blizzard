import { effect, inject } from '@angular/core';
import { QueryClient, type QueryObserverResult } from '@tanstack/angular-query-experimental';
import { injectQueries } from '@tanstack/angular-query-experimental/inject-queries-experimental';
import { errorMessage, runnerApi } from 'fleet';

import { runnerDashboardKey, runnerSelftestKey } from '../core/query-keys';

/** How often a still-`running` selftest run is re-read. */
export const SELFTEST_POLL_MS = 1000;

/** One harness's latest selftest run, by id. */
export interface SelftestRunRef {
  readonly harness: string;
  readonly id: string;
}

/** A harness's latest run as last read, or the read's failure. */
export interface SelftestRunRead {
  readonly run: runnerApi.SelfTestView | null;
  readonly error: string | null;
}

/**
 * `GET /runners/{runner_id}/api/selftests/{selftest_id}` — one run's current state, through the
 * generated `getSelftestRunnersRunnerIdApiSelftestsSelftestIdGet` (`bzh:generated-client`). It is
 * re-read every {@link SELFTEST_POLL_MS} while `running`, and not again once terminal.
 */
export function selftestQueryOptions(runnerId: string, selftestId: string) {
  return {
    queryKey: runnerSelftestKey(runnerId, selftestId),
    queryFn: async (): Promise<runnerApi.SelfTestView> => {
      const { data, error } = await runnerApi.getSelftestRunnersRunnerIdApiSelftestsSelftestIdGet({
        path: { runner_id: runnerId, selftest_id: selftestId },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    refetchInterval: (query: { state: { data?: runnerApi.SelfTestView } }) =>
      query.state.data?.status === 'running' ? SELFTEST_POLL_MS : false,
  };
}

/**
 * Each harness's latest run on `runnerId` ({@link selftestQueryOptions}), keyed by harness. A run
 * that reads back terminal re-reads the runner's dashboard once, since its `harness_health` changes.
 */
export function injectSelftestRunsQuery(runnerId: () => string, runs: () => readonly SelftestRunRef[]) {
  const queryClient = inject(QueryClient);
  const reads = injectQueries(() => ({
    queries: runs().map((ref) => ({
      ...selftestQueryOptions(runnerId(), ref.id),
      select: (run: runnerApi.SelfTestView) => ({ harness: ref.harness, run }),
    })),
    combine: (results) => {
      const plain = results as unknown as readonly QueryObserverResult<{ harness: string; run: runnerApi.SelfTestView }>[];
      return new Map<string, SelftestRunRead>(runs().map((ref, i) => {
        const result = plain[i];
        return [ref.harness, {
          run: result?.data?.run ?? null,
          error: result?.isError ? errorMessage(result.error, 'Selftest read failed.') : null,
        }] as const;
      }));
    },
  }));
  const settled = new Set<string>();
  effect(() => {
    for (const read of reads().values()) {
      const run = read.run;
      if (run === null || run.status === 'running' || settled.has(run.id)) continue;
      settled.add(run.id);
      void queryClient.invalidateQueries({ queryKey: runnerDashboardKey(runnerId()) });
    }
  });
  return reads;
}
