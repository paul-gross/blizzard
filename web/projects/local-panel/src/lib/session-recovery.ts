import { Injectable, signal } from '@angular/core';
import { runnerApi } from 'fleet';

import { runnerLogoutInFlight } from './auth.query';

/** `sessionStorage` key marking that a bounce was already attempted this cycle
 * (issue #312) — the backstop `handle` checks across the full-page navigation
 * it drives: a no-session `401` classified while this is still set did not get
 * fixed by the last attempt, so the seam surfaces {@link SessionRecovery.recovering}
 * instead of navigating again. Cleared only by a session read that resolves a
 * username (D4) — the one proof the bounce actually worked. */
const RENEWAL_MARK_KEY = 'blizzard.runner.session-renewal-attempted';

const SESSION_PATH = '/api/auth/session';

function markSet(): boolean {
  return sessionStorage.getItem(RENEWAL_MARK_KEY) !== null;
}

function setMark(): void {
  sessionStorage.setItem(RENEWAL_MARK_KEY, '1');
}

function clearMark(): void {
  sessionStorage.removeItem(RENEWAL_MARK_KEY);
}

function isSessionRead(request: Request): boolean {
  return new URL(request.url).pathname === SESSION_PATH;
}

/** `GET /api/auth/login?return_to=…` for the live route, built from `location`
 * so the target is same-origin by construction (D1). */
function loginUrl(): string {
  const { pathname, search } = globalThis.location;
  return `/api/auth/login?return_to=${encodeURIComponent(pathname + search)}`;
}

/**
 * The runner webapp's session-recovery seam (issue #312) — the response interceptor
 * body `provideSessionRecovery` (`session-recovery.provider.ts`) registers on
 * `runnerClient`. Classifies every `401` the client sees and drives the federation
 * bounce for the one case it can fix: a gated surface whose runner session has
 * expired. Every other `401` — an
 * upstream rejection with a resolved username, or an authless surface — passes
 * through untouched, left to degrade in its own region exactly as it does today
 * (`chunk-title.query.ts` et al.).
 *
 * Two guards keep a session drop from looping (D4): an in-memory single-flight flag
 * coalesces the burst of `401`s the panel's concurrent polls produce into one
 * classification, and the `sessionStorage` mark above survives the navigation itself
 * — a further no-session `401` arriving while it is still set sets
 * {@link recovering} instead of firing a second bounce. A logout already in flight
 * ({@link runnerLogoutInFlight}) suspends the seam entirely: that flow clears the
 * session deliberately and drives its own navigation once it settles.
 */
@Injectable({ providedIn: 'root' })
export class SessionRecovery {
  private readonly attemptFailed = signal(false);

  /** Set once a bounce was already attempted (the mark is set) and a further
   * no-session `401` arrives before it completes — the one condition the
   * recovery view (Phase 2) renders for. */
  readonly recovering = this.attemptFailed.asReadonly();

  private inFlight = false;

  /** Always resolves to `response` unchanged — the seam only ever observes and
   * reacts, it never transforms what a caller sees. */
  async handle(response: Response, request: Request): Promise<Response> {
    if (isSessionRead(request) && response.ok) {
      const body = (await response.clone().json()) as runnerApi.RunnerAuthSessionView;
      if (body.auth_enabled && body.username) {
        clearMark();
        this.attemptFailed.set(false);
      }
      return response;
    }

    if (response.status !== 401 || runnerLogoutInFlight() || this.inFlight) return response;

    this.inFlight = true;
    try {
      const { data } = await runnerApi.readSessionApiAuthSessionGet({ throwOnError: false });
      const noSession = data !== undefined && data.auth_enabled && !data.username;
      if (!noSession) return response;

      if (markSet()) {
        this.attemptFailed.set(true);
      } else {
        setMark();
        this.navigate(loginUrl());
      }
    } finally {
      this.inFlight = false;
    }
    return response;
  }

  /** The recovery view's retry action — clears the mark first so a `401` racing
   * the navigation cannot strand the operator behind the view a second time. */
  retry(): void {
    clearMark();
    this.attemptFailed.set(false);
    this.navigate(loginUrl());
  }

  /** Full-page navigation to the federation bounce — factored out so specs can
   * spy it, the way `local-identity.spec.ts` spies `reload`. */
  protected navigate(url: string): void {
    globalThis.location.assign(url);
  }
}
