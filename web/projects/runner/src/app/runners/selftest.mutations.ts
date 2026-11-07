import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';
import { runnerApi } from 'fleet';

import { selftestStartMutationKey } from '../core/mutation-keys';
import { runnerDashboardKey } from '../core/query-keys';

/** Start a selftest of `harness` on `runnerId`. */
export interface SelftestStartVars {
  readonly runnerId: string;
  readonly harness: string;
}

/** What one start's own outcome is handed to — each invocation's, with its own variables. */
export interface SelftestStartHandlers {
  readonly onSuccess: (run: runnerApi.SelfTestView, vars: SelftestStartVars) => void;
  readonly onError: (error: unknown, vars: SelftestStartVars) => void;
}

/**
 * `POST /runners/{runner_id}/api/selftests` — the route `blizzard runner selftest` uses, called
 * through the generated `startSelftestRunnersRunnerIdApiSelftestsPost` (`bzh:generated-client`).
 * It answers the minted run, still `running`; an unknown harness is a 422 and an unavailable one a
 * 503. Each start's outcome reaches `handlers` at the mutation level, so overlapping starts each
 * report their own. On settle it re-reads the runner's dashboard, whose `harness_health` the run feeds.
 */
export function injectSelftestStartMutation(runnerId: () => string, handlers: SelftestStartHandlers) {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: selftestStartMutationKey(runnerId()),
    mutationFn: async (vars: SelftestStartVars): Promise<runnerApi.SelfTestView> => {
      const { data, error } = await runnerApi.startSelftestRunnersRunnerIdApiSelftestsPost({
        path: { runner_id: vars.runnerId },
        body: { harness: vars.harness },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSuccess: (run, vars) => handlers.onSuccess(run, vars),
    onError: (error, vars) => handlers.onError(error, vars),
    onSettled: (_data, _error, vars) => queryClient.invalidateQueries({ queryKey: runnerDashboardKey(vars.runnerId) }),
  }));
}
