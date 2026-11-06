import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { enableCadenceApiCadencesCadenceIdEnablePost, retireCadenceApiCadencesCadenceIdRetirePost } from 'fleet';
import { cadenceLifecycleMutationKey } from '../../core/mutation-keys';
import { hubCadenceErrandsKey, hubCadencesKey } from '../core/cadences.query';

/** Retire or re-enable a cadence — a reversible brake: a retired cadence mints nothing, and its errands all stop. */
export interface CadenceLifecycleVars {
  readonly cadenceId: string;
  readonly revision: number;
  readonly retired: boolean;
}

/**
 * `POST /api/cadences/{cadence_id}/retire|enable` with `If-Match: <revision>`, routed by the desired `retired` state.
 * Settles on the refetch of the cadence list and that cadence's errands.
 */
export function injectCadenceLifecycleMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: cadenceLifecycleMutationKey,
    mutationFn: async (vars: CadenceLifecycleVars): Promise<void> => {
      const call = vars.retired
        ? retireCadenceApiCadencesCadenceIdRetirePost
        : enableCadenceApiCadencesCadenceIdEnablePost;
      const { error } = await call({
        path: { cadence_id: vars.cadenceId },
        headers: { 'if-match': vars.revision },
        body: { by: 'operator' },
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
