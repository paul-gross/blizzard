import { Signal } from '@angular/core';
import { injectMutationState } from '@tanstack/angular-query-experimental';

/** The variables of every mutation matching `mutationKey` that is still `pending` — filtered to
 * `pending` rather than left unfiltered because a settled mutation's variables would otherwise
 * linger in the list after the mutation itself no longer justifies an overlay. */
export function injectPendingMutationVariables<TVars>(mutationKey: readonly unknown[]): Signal<TVars[]> {
  return injectMutationState(() => ({
    filters: { mutationKey, status: 'pending' },
    select: (mutation) => mutation.state.variables as TVars,
  }));
}

export function isPendingFor<TVars>(pending: TVars[], predicate: (vars: TVars) => boolean): boolean {
  return pending.some(predicate);
}
