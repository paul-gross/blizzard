// exemplar: bzh:frontend-mutation-settles-on-refresh, bzh:frontend-pending-override — keyed hooks, settled by refetch.

import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import {
  type PermitVerb,
  type PermitView,
  grantPermitApiPermitsPermitIdGrantPost,
  hubPermitKey,
  hubPermitsKey,
  resumePermitApiPermitsPermitIdResumePost,
  revokePermitApiPermitsPermitIdRevokePost,
  suspendPermitApiPermitsPermitIdSuspendPost,
} from 'fleet';
import { permitMoveMutationKey, permitRevokeMutationKey } from '../core/mutation-keys';

/** A verb that moves a permit without a body. */
export type PermitMove = Exclude<PermitVerb, 'revoke'>;

/** A move's variables: the permit and the verb applied to it. */
export interface PermitMoveVars {
  readonly permitId: string;
  readonly verb: PermitMove;
}

/** A revocation's variables: the permit and why it is revoked. */
export interface PermitRevokeVars {
  readonly permitId: string;
  readonly note: string;
}

const MOVE_CALLS = {
  grant: grantPermitApiPermitsPermitIdGrantPost,
  suspend: suspendPermitApiPermitsPermitIdSuspendPost,
  resume: resumePermitApiPermitsPermitIdResumePost,
} as const;

/** `POST /api/permits/{permit_id}/grant|suspend|resume`, chosen by `verb`. A refusal rejects. */
export function injectPermitMoveMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: permitMoveMutationKey,
    mutationFn: async (vars: PermitMoveVars): Promise<PermitView> => {
      const { data, error } = await MOVE_CALLS[vars.verb]({ path: { permit_id: vars.permitId }, throwOnError: false });
      if (error) throw error;
      return data!;
    },
    onSettled: (_data, _error, vars) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: hubPermitsKey }),
        queryClient.invalidateQueries({ queryKey: hubPermitKey(vars.permitId) }),
      ]),
  }));
}

/** `POST /api/permits/{permit_id}/revoke` — revoke a permit with a note. A refusal rejects. */
export function injectPermitRevokeMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: permitRevokeMutationKey,
    mutationFn: async (vars: PermitRevokeVars): Promise<PermitView> => {
      const { data, error } = await revokePermitApiPermitsPermitIdRevokePost({
        path: { permit_id: vars.permitId },
        body: { note: vars.note },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSettled: (_data, _error, vars) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: hubPermitsKey }),
        queryClient.invalidateQueries({ queryKey: hubPermitKey(vars.permitId) }),
      ]),
  }));
}
