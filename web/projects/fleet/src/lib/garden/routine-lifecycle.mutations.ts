import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { enableRoutineApiRoutinesRoutineIdEnablePost, retireRoutineApiRoutinesRoutineIdRetirePost } from '../api/hub';
import { routineLifecycleMutationKey } from '../mutation-keys';
import { hubRoutinesKey } from '../query-keys';

/** Retire or re-enable a routine's reversible brake: a retired routine is refused a new
 * run and excluded from the default list, but its runs, findings, proposals, and
 * closures stay live, queryable, and attributable throughout — `ScopeLifecycleVars`'s
 * own shape. */
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
