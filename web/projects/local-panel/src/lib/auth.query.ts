import { inject } from '@angular/core';
import { injectMutation, injectQuery, QueryClient } from '@tanstack/angular-query-experimental';
import { runnerApi } from 'fleet';

import { runnerSessionKey } from './query-keys';

/**
 * `GET /api/auth/session` (issue #129) — the panel's own-identity read behind its
 * username/logout control. Self-resolving and never `401` (the runner reports the
 * identity a request *would* resolve to), so the query never errors on "not signed
 * in": under a `none`-mode hub it answers `auth_enabled: false` (authless surface —
 * hide the control), under oauth it carries the signed-in hub `username` (or `null`
 * when no session rode along). Same 5s poll floor as the other local reads.
 */
export function injectRunnerSessionQuery() {
  return injectQuery(() => ({
    queryKey: runnerSessionKey,
    queryFn: async (): Promise<runnerApi.RunnerAuthSessionView> => {
      const { data, error } = await runnerApi.readSessionApiAuthSessionGet({ throwOnError: false });
      if (error) throw error;
      return data!;
    },
    refetchInterval: 5000,
  }));
}

/**
 * `POST /api/auth/logout` (issue #129) — clears the runner's own session cookie, then
 * invalidates the session read so the control drops the username. The runner session
 * is a stateless signed cookie, so this is the whole logout; SSO stays honest — the
 * caller reloads so the served shell's gate decides the next visit (a still-live hub
 * session re-authenticates silently through the bounce; an ended one lands on the hub's
 * login surface). Mirrors the hub's own `injectLogoutMutation`.
 */
export function injectRunnerLogoutMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (): Promise<void> => {
      const { error } = await runnerApi.logoutApiAuthLogoutPost({ throwOnError: false });
      if (error) throw error;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: runnerSessionKey });
    },
  }));
}
