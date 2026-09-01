import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { type RoutineRunResponse, runRoutineApiRoutinesRoutineIdRunPost } from '../api/hub';
import { hubRoutinesKey } from '../query-keys';

/** Kick off a routine run — the gardening run dialog's own submission (D3's create-then
 * -run ordering runs this second, once any new scope's create has already succeeded). */
export interface RoutineRunVars {
  readonly routineId: string;
  readonly scopeSlug: string;
  readonly mode: 'full' | 'delta';
  readonly note: string | null;
}

/**
 * `POST /api/routines/{routine_id}/run` through the generated client
 * (bzh:generated-client) — mints, ingests, and promotes a hub work item from the
 * routine in one act. A requested `delta` with no recorded baseline downgrades to
 * `full` on the response rather than refusing (`RoutineRunResponse.downgraded`); the
 * dialog itself never submits `delta` for a never-swept pair (the baselines read
 * steers it to `full` first), so a downgrade here would be racing another writer. On
 * success, invalidates the routine list — a run leaves no field on the routine record
 * unchanged apart from usage the fleet views re-read on their own.
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
