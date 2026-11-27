// exemplar: bzh:frontend-mutation-settles-on-refresh — the edit settles only once the read it changed has refetched.

import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { type CadenceEditRequest, editCadenceApiCadencesCadenceIdPatch } from 'fleet/hub-api';
import { editCadenceMutationKey } from '../core/mutation-keys';
import { hubCadenceEditListKey } from '../core/query-keys';

/** An edit of one cadence, sent against the revision it was read at. */
export interface CadenceEditVars {
  readonly cadenceId: string;
  readonly revision: number;
  readonly body: CadenceEditRequest;
}

/** `PATCH /api/cadences/{cadence_id}` with `If-Match: <revision>`, so an edit against a stale read is refused with a
 * 409 rather than overwriting a concurrent one. Settles on the refetch of the cadence list. */
export function injectEditCadenceMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: editCadenceMutationKey,
    mutationFn: async (vars: CadenceEditVars): Promise<void> => {
      const { error } = await editCadenceApiCadencesCadenceIdPatch({
        path: { cadence_id: vars.cadenceId },
        headers: { 'if-match': vars.revision },
        body: vars.body,
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubCadenceEditListKey }),
  }));
}
