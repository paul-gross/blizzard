import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { editRoutineApiRoutinesRoutineIdPatch, type hubApi } from 'fleet';
import { editRoutineMutationKey } from '../../core/mutation-keys';
import { hubRoutinesKey } from '../../core/query-keys';

/** An edit of one routine, sent against the revision it was read at. */
export interface RoutineEditVars {
  readonly routineId: string;
  readonly revision: number;
  readonly body: hubApi.RoutineEditRequest;
}

/**
 * `PATCH /api/routines/{routine_id}` with `If-Match: <revision>`, so an edit made against a stale read is refused with
 * a 409 rather than overwriting a concurrent one. On settling it re-reads the routine list.
 */
export function injectEditRoutineMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: editRoutineMutationKey,
    mutationFn: async (vars: RoutineEditVars): Promise<void> => {
      const { error } = await editRoutineApiRoutinesRoutineIdPatch({
        path: { routine_id: vars.routineId },
        headers: { 'if-match': vars.revision },
        body: vars.body,
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubRoutinesKey }),
  }));
}
