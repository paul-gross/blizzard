import { inject, signal } from '@angular/core';
import { injectMutation, injectQuery, QueryClient } from '@tanstack/angular-query-experimental';
import { runnerApi } from 'fleet';

import { runnerSessionKey } from './query-keys';

const logoutInFlightSignal = signal(false);

/** Whether `POST /api/auth/logout` is currently in flight (issue #312) — set for
 * the duration of {@link injectRunnerLogoutMutation}'s call, the panel's only
 * logout driver. The session-recovery seam (`session-recovery.ts`) suspends on
 * this: a `401` arriving mid-logout is the deliberate session clear, not an
 * expiry to renew, and logout already drives its own navigation once it settles. */
export const runnerLogoutInFlight = logoutInFlightSignal.asReadonly();

/**
 * `GET /api/auth/session` (issue #129) — the panel's own-identity read behind its
 * username/logout control. Self-resolving and never `401` (the runner reports the
 * identity a request *would* resolve to), so the query never errors on "not signed
 * in": under a `none`-mode hub it answers `auth_enabled: false` (authless surface —
 * hide the control), under oauth it carries the signed-in hub `username` (or `null`
 * when no session rode along). No `refetchInterval` (D7, blizzard#317 Phase 4): the
 * poll this used to carry stood in for a session-loss signal; the runner's own auth
 * dependency (`require_human_api`) resolves once when the stream connects and is
 * never re-checked per frame, so an in-place expiry surfaces through whichever
 * backstop-polled read (leases/status/chunk-detail) next re-authenticates over HTTP
 * and gets a `401` — the stream itself only 401s on a reconnect (daemon restart,
 * transport drop). Either source lands on `session-recovery.ts`'s seam, which drives
 * a full-page federation bounce that re-reads every key, this one included, on the
 * way back. The one other writer, the logout mutation below, invalidates this key
 * explicitly on success, so a deliberate session clear is never left stale either.
 */
export function injectRunnerSessionQuery() {
  return injectQuery(() => ({
    queryKey: runnerSessionKey,
    queryFn: async (): Promise<runnerApi.RunnerAuthSessionView> => {
      const { data, error } = await runnerApi.readSessionApiAuthSessionGet({ throwOnError: false });
      if (error) throw error;
      return data!;
    },
  }));
}

/**
 * The signed-in hub username a session read carries, or `null` when there is none
 * to show — a `none`-mode hub (authless surface), an unresolved read, or oauth
 * with no session riding along.
 *
 * One owner for the fold because two components gate on it: {@link LocalIdentity}
 * renders itself off it, and the mobile shell gates its own `Log out` **menu
 * item** off it. The shell cannot reuse the identity block's copy — that
 * component is constructed inside the menu overlay, so its signal is still
 * unresolved on the overlay's first change detection, and a menu item that
 * appears a tick late is one `CdkMenu` has already skipped when setting initial
 * focus. The shell reads the same query itself, long resolved by the time a menu
 * opens; this keeps the two answers from drifting.
 */
export function signedInUsername(session: runnerApi.RunnerAuthSessionView | undefined): string | null {
  return session?.auth_enabled ? (session.username ?? null) : null;
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
    onMutate: () => logoutInFlightSignal.set(true),
    onSettled: () => logoutInFlightSignal.set(false),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: runnerSessionKey });
    },
  }));
}
