import { inject, signal } from '@angular/core';
import { injectMutation, injectQuery, QueryClient } from '@tanstack/angular-query-experimental';
import { runnerApi } from 'fleet/runner-shell';

import { runnerLogoutMutationKey } from '../mutation-keys';
import { sessionKey } from '../query-keys';
import type { RunnerSession } from './session.domain';
import { fromRunnerAuthSessionView } from './session.translate';

const logoutInFlightSignal = signal(false);

/** Whether `POST /api/auth/logout` is currently in flight — set for
 * the duration of {@link injectRunnerLogoutMutation}'s call, the panel's only
 * logout driver. See `session-recovery.ts` for how a `401` is treated while
 * this holds. */
export const runnerLogoutInFlight = logoutInFlightSignal.asReadonly();

/**
 * `GET /api/auth/session` — the panel's own-identity read behind its
 * username/logout control. Self-resolving and never `401` (the runner reports the
 * identity a request *would* resolve to), so the query never errors on "not signed
 * in": under a `none`-mode hub it answers `auth_enabled: false` (authless surface —
 * hide the control), under oauth it carries the signed-in hub `username` (or `null`
 * when no session rode along). No `refetchInterval`, so no poll stands in for a
 * session-loss signal; the runner's own auth
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
    queryKey: sessionKey,
    queryFn: async (): Promise<runnerApi.RunnerAuthSessionView> => {
      const { data, error } = await readRunnerSession();
      if (error) throw error;
      return data!;
    },
    select: fromRunnerAuthSessionView,
  }));
}

/**
 * One raw, uncached read of the runner's auth session — the wire call behind
 * {@link injectRunnerSessionQuery}, exported for the one caller that must classify
 * a fresh answer outside the query cache (`session-recovery.ts`'s seam). `data` is
 * `undefined` when the read itself failed.
 */
export function readRunnerSession() {
  return runnerApi.readSessionApiAuthSessionGet({ throwOnError: false });
}

/** {@link readRunnerSession} as a {@link RunnerSession}, `undefined` when the read itself failed. */
export async function readRunnerSessionDomain(): Promise<RunnerSession | undefined> {
  const { data } = await readRunnerSession();
  return data === undefined ? undefined : fromRunnerAuthSessionView(data);
}

/** The {@link RunnerSession} a successful `GET /api/auth/session` response carries, read from a clone so the
 * caller's response stays unconsumed. */
export async function sessionOfResponse(response: Response): Promise<RunnerSession> {
  return fromRunnerAuthSessionView((await response.clone().json()) as runnerApi.RunnerAuthSessionView);
}

/**
 * `POST /api/auth/logout` — clears the runner's own session cookie, then
 * invalidates the session read so the control drops the username. The runner session
 * is a stateless signed cookie, so this is the whole logout.
 */
export function injectRunnerLogoutMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationKey: runnerLogoutMutationKey,
    mutationFn: async (): Promise<void> => {
      const { error } = await runnerApi.logoutApiAuthLogoutPost({ throwOnError: false });
      if (error) throw error;
    },
    onMutate: () => logoutInFlightSignal.set(true),
    onSettled: () => {
      logoutInFlightSignal.set(false);
      return queryClient.invalidateQueries({ queryKey: sessionKey });
    },
  }));
}
