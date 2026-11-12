import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { editRoutineApiRoutinesRoutineIdPatch, hubRoutinesKey } from 'fleet';
import { editRoutineMutationKey } from '../../core/mutation-keys';

/** Change a routine’s graph or default urgency without touching its other fields. */
export interface RoutineEditVars {
  readonly routineId: string;
  readonly graphName?: string;
  readonly defaultUrgency?: string | null;
}

/**
 * `PATCH /api/routines/{routine_id}` with the stated fields — through the generated client
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
        body: { ...(vars.graphName !== undefined ? { graph_name: vars.graphName } : {}), ...(vars.defaultUrgency !== undefined ? { default_urgency: vars.defaultUrgency } : {}) },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubRoutinesKey }),
  }));
}
