import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { type UserView, assignRoleApiUsersUserIdRolePost } from '../api/hub';
import { hubUsersKey } from '../query-keys';

/** `POST /api/users/{id}/role`'s own variables — the target user and the role it is
 * being assigned to. Every hub-side rule (self-change, `superuser` grant/revoke,
 * `superuser` not assignable) is enforced server-side (`AuthService.assign_role`);
 * this mutation surfaces a refusal as its own error state rather than re-deriving
 * the rules client-side. */
export interface AssignRoleVars {
  readonly userId: string;
  readonly role: string;
}

/**
 * `POST /api/users/{id}/role` — assign a hub-local user a new role (issue #94),
 * through the generated client (bzh:generated-client). On success it invalidates the
 * user listing so the table re-reads the change; the subject's *own* next request
 * already resolves the new role server-side (no client-side propagation needed).
 */
export function injectAssignRoleMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: AssignRoleVars): Promise<UserView> => {
      const { data, error } = await assignRoleApiUsersUserIdRolePost({
        path: { user_id: vars.userId },
        body: { role: vars.role },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: hubUsersKey });
    },
  }));
}
