import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { enableRoutineApiRoutinesRoutineIdEnablePost, retireRoutineApiRoutinesRoutineIdRetirePost } from 'fleet';
import { routineLifecycleMutationKey } from '../../core/mutation-keys';
import { hubRoutinesKey } from '../../core/query-keys';

/** Retire or re-enable a routine's reversible brake. */
export interface RoutineLifecycleVars {
  readonly routineId: string;
  /** The revision the confirmation opened on — sent as `If-Match`. */
  readonly revision: number | null;
  readonly retired: boolean;
}

/**
 * `POST /api/routines/{routine_id}/retire|enable` with `If-Match: <revision>` when the routine has one — routed by the desired `retired`
 * state (`injectScopeLifecycleMutation`'s own shape), through the generated client
 * (bzh:generated-client). On success it re-reads the routine list. The hub records
 * the signed-in caller as the actor.
 */
export function injectRoutineLifecycleMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: routineLifecycleMutationKey,
    mutationFn: async (vars: RoutineLifecycleVars): Promise<void> => {
      const call = vars.retired ? retireRoutineApiRoutinesRoutineIdRetirePost : enableRoutineApiRoutinesRoutineIdEnablePost;
      const { error } = await call({
        path: { routine_id: vars.routineId },
        ...(vars.revision === null ? {} : { headers: { 'if-match': vars.revision } }),
        body: {},
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubRoutinesKey }),
  }));
}
