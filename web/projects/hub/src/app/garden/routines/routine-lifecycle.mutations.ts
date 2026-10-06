import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { enableRoutineApiRoutinesRoutineIdEnablePost, retireRoutineApiRoutinesRoutineIdRetirePost, hubRoutinesKey } from 'fleet';
import { routineLifecycleMutationKey } from '../../core/mutation-keys';

/** Retire or re-enable a routine's reversible brake — `ScopeLifecycleVars`'s own
 * shape. */
export interface RoutineLifecycleVars {
  readonly routineId: string;
  readonly retired: boolean;
}

/**
 * `POST /api/routines/{routine_id}/retire|enable` — routed by the desired `retired`
 * state (`injectScopeLifecycleMutation`'s own shape), through the generated client
 * (bzh:generated-client). On success it re-reads the routine list. `by` defaults to
 * `operator` server-side.
 */
export function injectRoutineLifecycleMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: routineLifecycleMutationKey,
    mutationFn: async (vars: RoutineLifecycleVars): Promise<void> => {
      const call = vars.retired ? retireRoutineApiRoutinesRoutineIdRetirePost : enableRoutineApiRoutinesRoutineIdEnablePost;
      const { error } = await call({
        path: { routine_id: vars.routineId },
        body: { by: 'operator' },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubRoutinesKey }),
  }));
}
