import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { type RoutineRunResponse, runRoutineApiRoutinesRoutineIdRunPost } from '../api/hub';
import { hubRoutinesKey } from '../query-keys';

/** Kick off a routine run — the gardening run dialog's own submission. The
 * create-then-run ordering (D3) is `GardeningRunDialog.onSubmit`'s own fact, not
 * restated here. */
export interface RoutineRunVars {
  readonly routineId: string;
  readonly scopeSlug: string;
  readonly mode: 'full' | 'delta';
  readonly note: string | null;
}

/**
 * `POST /api/routines/{routine_id}/run` through the generated client
 * (bzh:generated-client) — mints, ingests, and promotes a hub work item from the
 * routine in one act. Submits exactly the mode it is given and resolves no baseline
 * itself; a requested `delta` with no recorded baseline downgrades to `full` on the
 * response rather than refusing (`RoutineRunResponse.downgraded`). On success,
 * invalidates the routine list — a run leaves no field on the routine record unchanged
 * apart from usage the fleet views re-read on their own.
 */
export function injectRunRoutineMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: RoutineRunVars): Promise<RoutineRunResponse> => {
      const { data, error } = await runRoutineApiRoutinesRoutineIdRunPost({
        path: { routine_id: vars.routineId },
        body: { scope_slug: vars.scopeSlug, mode: vars.mode, note: vars.note },
        throwOnError: false,
      });
      if (error) throw error;
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: hubRoutinesKey });
    },
  }));
}
