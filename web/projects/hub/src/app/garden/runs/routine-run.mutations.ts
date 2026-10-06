import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { type RoutineRunResponse, runRoutineApiRoutinesRoutineIdRunPost, hubRoutinesKey } from 'fleet';
import { runRoutineMutationKey } from '../../core/mutation-keys';

/** Variables for running an existing routine against a scope in a given mode. */
export interface RoutineRunVars {
  readonly routineId: string;
  readonly scopeSlug: string;
  readonly mode: 'full' | 'delta';
  readonly note: string | null;
}

/**
 * `POST /api/routines/{routine_id}/run` through the generated client
 * (bzh:generated-client); the run semantics are the route's own
 * (`RoutineRunResponse`). Submits exactly the mode it is given and resolves no baseline
 * itself. On success,
 * invalidates the routine list — a run leaves no field on the routine record unchanged
 * apart from usage the fleet views re-read on their own.
 */
export function injectRunRoutineMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: runRoutineMutationKey,
    mutationFn: async (vars: RoutineRunVars): Promise<RoutineRunResponse> => {
      const { data, error } = await runRoutineApiRoutinesRoutineIdRunPost({
        path: { routine_id: vars.routineId },
        body: { scope_slug: vars.scopeSlug, mode: vars.mode, note: vars.note },
        throwOnError: false,
      });
      if (error) throw error;
      return data;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubRoutinesKey }),
  }));
}
