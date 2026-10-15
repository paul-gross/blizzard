import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { editRoutineApiRoutinesRoutineIdPatch, hubRoutinesKey } from 'fleet';
import { editRoutineMutationKey } from '../../core/mutation-keys';

/** Change the graph a routine runs — its strategy — in place; never touches its name. */
export interface RoutineEditVars {
  readonly routineId: string;
  readonly graphName: string;
}

/**
 * `PATCH /api/routines/{routine_id}` with `{ graph_name }` — through the generated client
 * (bzh:generated-client), `injectEditScopeMutation`'s own shape. On settling it re-reads the
 * routine list, the detail's only routine read.
 */
export function injectEditRoutineMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: editRoutineMutationKey,
    mutationFn: async (vars: RoutineEditVars): Promise<void> => {
      const { error } = await editRoutineApiRoutinesRoutineIdPatch({
        path: { routine_id: vars.routineId },
        body: { graph_name: vars.graphName },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubRoutinesKey }),
  }));
}
