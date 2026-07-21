import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import { logoutApiAuthLogoutPost } from '../api/hub';
import { hubMeKey } from '../query-keys';

/**
 * `POST /api/auth/logout` (issue #93) — revokes the session at the hub and clears the
 * cookie server-side, then drops the cached identity so the next `/api/me` read (the
 * app root's own gating query) resolves unauthenticated and the app renders the login
 * page. Under `auth.mode = "none"` this route still 204s (it always clears the
 * cookie) with nothing to revoke; the gating query still resolves to the implicit
 * operator afterward (no session to lose), matching "logout" having no visible effect
 * when there was never a login to begin with.
 */
export function injectLogoutMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (): Promise<void> => {
      const { error } = await logoutApiAuthLogoutPost({ throwOnError: false });
      if (error) throw error;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: hubMeKey });
    },
  }));
}
