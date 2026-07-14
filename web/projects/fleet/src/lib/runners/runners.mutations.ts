import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import {
  type RunnerView,
  pauseRunnerApiRunnersRunnerIdPausePost,
  resumeRunnerApiRunnersRunnerIdResumePost,
} from '../api/hub';
import { hubRunnersKey } from '../query-keys';

/** Toggle a runner's operator brake (D-043): pause stops new leases, resume clears it. */
export interface RunnerPauseVars {
  readonly runnerId: string;
  readonly paused: boolean;
}

/**
 * `POST /api/runners/{id}/pause|resume` — the operator brake (D-043), routed to the
 * pause or resume verb by the desired `paused` state, through the generated client
 * (bzh:generated-client). Re-reads the registry on success; the stream also fires
 * `runner-changed`. `by` defaults to `operator` server-side.
 */
export function injectRunnerPauseMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: RunnerPauseVars): Promise<RunnerView> => {
      const call = vars.paused ? pauseRunnerApiRunnersRunnerIdPausePost : resumeRunnerApiRunnersRunnerIdResumePost;
      const { data, error } = await call({
        path: { runner_id: vars.runnerId },
        body: { by: 'operator' },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: hubRunnersKey });
    },
  }));
}
