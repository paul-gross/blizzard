import { Injectable, signal } from '@angular/core';
import type { runnerApi } from 'fleet';

import { readRunnerSession, runnerLogoutInFlight } from './auth.query';

/** `sessionStorage` key marking that a bounce was already attempted this cycle. It
 * survives the navigation, so a no-session `401` classified while it is set sets
 * {@link SessionRecovery.recovering} instead of navigating again. Cleared only by a
 * session read that resolves a username. */
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
 * so the target is same-origin by construction. */
function loginUrl(): string {
  const { pathname, search } = globalThis.location;
  return `/api/auth/login?return_to=${encodeURIComponent(pathname + search)}`;
}

/**
 * {@link SessionRecovery.recoverFromUnauthenticated}'s classification outcome. Only
 * `read-failed` — the session read itself failed — is worth retrying: `skipped` is
 * the in-flight guard, `not-applicable` is a `401` this seam does not own, and
 * `bounced`/`already-recovering` are a no-session `401` already answered.
 */
export type RecoveryOutcome = 'skipped' | 'not-applicable' | 'read-failed' | 'bounced' | 'already-recovering';

/**
 * The runner webapp's session-recovery seam. Classifies a `401` and drives the
 * federation bounce for the one case it can fix: a gated surface whose runner
 * session has expired. Every other `401` passes through untouched.
 *
 * Two guards keep a session drop from looping: an in-memory single-flight flag
 * coalesces a burst of `401`s into one classification, and the `sessionStorage`
 * mark above survives the navigation, so a repeat sets {@link recovering} instead
 * of bouncing again. A logout in flight ({@link runnerLogoutInFlight}) suspends
 * the seam entirely.
 */
@Injectable({ providedIn: 'root' })
export class SessionRecovery {
  private readonly attemptFailed = signal(false);

  /** Set once a bounce was already attempted (the mark is set) and a further
   * no-session `401` arrives before it completes — the one condition the
   * recovery view renders for. */
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

    if (response.status === 401) await this.recoverFromUnauthenticated();
    return response;
  }

  /**
   * Classify a `401` by re-reading `GET /api/auth/session`, and drive the federation
   * bounce for a no-session `401` while the surface is gated. Needs no
   * `Response`/`Request`, and returns its {@link RecoveryOutcome} so a caller that
   * cannot render {@link recovering} can tell a failed session read apart from a
   * definitive answer.
   */
  async recoverFromUnauthenticated(): Promise<RecoveryOutcome> {
    if (runnerLogoutInFlight() || this.inFlight) return 'skipped';

    this.inFlight = true;
    try {
      const { data } = await readRunnerSession();
      if (data === undefined) return 'read-failed';
      const noSession = data.auth_enabled && !data.username;
      if (!noSession) return 'not-applicable';

      if (markSet()) {
        this.attemptFailed.set(true);
        return 'already-recovering';
      }
      setMark();
      this.navigate(loginUrl());
      return 'bounced';
    } finally {
      this.inFlight = false;
    }
  }

  /** The recovery view's retry action — clears the mark first so a `401` racing
   * the navigation cannot strand the operator behind the view a second time. */
  retry(): void {
    clearMark();
    this.attemptFailed.set(false);
    this.navigate(loginUrl());
  }

  /** Full-page navigation to the federation bounce — a method of its own so specs can spy it. */
  protected navigate(url: string): void {
    globalThis.location.assign(url);
  }
}
