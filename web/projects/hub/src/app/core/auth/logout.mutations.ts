import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { logoutApiAuthLogoutPost, hubMeKey } from 'fleet/shell';
import { logoutMutationKey } from '../mutation-keys';

/**
 * `POST /api/auth/logout` — revokes the session at the hub and clears the
 * cookie server-side, then invalidates the cached identity (`hubMeKey`) so the next
 * `/api/me` read reflects the logout.
 */
export function injectLogoutMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: logoutMutationKey,
    mutationFn: async (): Promise<void> => {
      const { error } = await logoutApiAuthLogoutPost({ throwOnError: false });
      if (error) throw error;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: hubMeKey }),
  }));
}
