import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import {
  type MemberView,
  type hubApi,
  grantMembershipApiMembersUserIdPut,
  hubMembersKey,
  revokeMembershipApiMembersUserIdDelete,
} from 'fleet';
import { memberRoleMutationKey, revokeMemberMutationKey } from '../../core/mutation-keys';

/** A role change's variables: the member and the membership role they are given. */
export interface MemberRoleVars {
  readonly userId: string;
  readonly role: hubApi.Role;
}

/** A revocation's variables: the member whose membership ends. */
export interface RevokeMemberVars {
  readonly userId: string;
}

/**
 * `PUT /api/members/{user_id}` — give a member of the board's tenant another membership role. Every rule (your own
 * role, the tenant's last admin, a role no membership holds) is the hub's; a refusal rejects, and is the caller's error
 * state rather than re-derived here. Settling re-reads the listing, so each row's offered changes follow the new roles.
 */
export function injectMemberRoleMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: memberRoleMutationKey,
    mutationFn: async (vars: MemberRoleVars): Promise<MemberView> => {
      const { data, error } = await grantMembershipApiMembersUserIdPut({
        path: { user_id: vars.userId },
        body: { role: vars.role },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubMembersKey }),
  }));
}

/**
 * `DELETE /api/members/{user_id}` — end a member's membership in the board's tenant. The hub refuses your own and the
 * tenant's last admin; a refusal rejects, as {@link injectMemberRoleMutation}'s does. Settling re-reads the listing.
 */
export function injectRevokeMemberMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: revokeMemberMutationKey,
    mutationFn: async (vars: RevokeMemberVars): Promise<void> => {
      const { error } = await revokeMembershipApiMembersUserIdDelete({
        path: { user_id: vars.userId },
        throwOnError: false,
      });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubMembersKey }),
  }));
}
