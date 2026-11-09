import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import {
  type CadenceCreateRequest,
  type CadenceEditRequest,
  type CadenceView,
  createProjectCadenceApiProjectsProjectCadencesPost,
  editCadenceApiCadencesCadenceIdPatch,
} from 'fleet';
import { injectProjectFilter } from 'fleet/shell';
import { createCadenceMutationKey, editCadenceMutationKey } from '../../core/mutation-keys';
import { hubCadenceErrandsKey, hubCadencesKey } from '../core/cadences.query';
import { gardenProject } from '../core/garden-project';

/** An edit of one cadence, sent against the revision it was read at. A chore add, edit, or remove is an edit whose body
 * carries the whole `chores` list. */
export interface CadenceEditVars {
  readonly cadenceId: string;
  readonly revision: number;
  readonly body: CadenceEditRequest;
}

/**
 * `POST /api/projects/{project}/cadences` — creates a cadence in the lensed project and answers it. Settles on the
 * refetch of the cadence list.
 */
export function injectCreateCadenceMutation() {
  const queryClient = inject(QueryClient);
  const project = injectProjectFilter();
  return injectMutation(() => ({
    mutationKey: createCadenceMutationKey,
    mutationFn: async (body: CadenceCreateRequest): Promise<CadenceView> => {
      const { data, error } = await createProjectCadenceApiProjectsProjectCadencesPost({
        path: { project: gardenProject(project()) },
        body,
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubCadencesKey }),
  }));
}

/**
 * `PATCH /api/cadences/{cadence_id}` with `If-Match: <revision>`, so an edit made against a stale read is refused
 * with a 409 rather than overwriting a concurrent one. Settles on the refetch of the cadence list and that cadence's
 * errands.
 */
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
    onSettled: (_data, _error, vars) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: hubCadencesKey }),
        queryClient.invalidateQueries({ queryKey: hubCadenceErrandsKey(vars.cadenceId) }),
      ]),
  }));
}
