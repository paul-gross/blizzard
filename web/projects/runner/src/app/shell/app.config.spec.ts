import { TestBed } from '@angular/core/testing';
import { runnerApi, runnerClient } from 'fleet';
import {
  type RequestClientStub,
  stubError,
  stubRunnerClient,
} from 'fleet/testing';
import { SessionRecovery } from '../core/identity/session-recovery';
import { vi } from 'vitest';

import { appConfig } from './app.config';

/**
 * Proves `provideSessionRecovery()` is actually reachable through the runner
 * app's own production provider list — every other
 * `SessionRecovery` spec assembles its own providers (`session-recovery.spec.ts`),
 * which would stay green even if `app.config.ts` forgot to call
 * `provideSessionRecovery()`. no other tier pins this
 * (`bzh:gating-tier-pins-production-paths`), so this is the one `web:unit-test`
 * asserting against `appConfig.providers` itself rather than a hand-assembled list.
 */
/** One macrotask turn: every microtask-chained recovery a sibling left in flight settles within it. */
function drainLeakedRecovery(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

describe('runner appConfig wires session recovery', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    // This project's specs share one jsdom/module registry under
    // single-worker scheduling (Angular's unit-test builder defaults Vitest to
    // `isolate: false`), so a sibling spec's leaked renewal mark could otherwise
    // become this test's starting state. A sibling's 401 it never awaited can land
    // its `setMark()` a turn after that spec's own teardown, so give the event loop
    // a macrotask to land it before clearing — clearing first would let it arrive
    // against this test's clean slate and turn its bounce into `already-recovering`.
    await drainLeakedRecovery();
    sessionStorage.clear();
  });

  afterEach(async () => {
    stub.restore();
    runnerClient.interceptors.response.clear();
    await drainLeakedRecovery();
    sessionStorage.clear();
  });

  it('navigates to the federation bounce on a no-session 401 taken through runnerClient', async () => {
    TestBed.configureTestingModule({ providers: appConfig.providers });
    // Forces the environment injector — and its ENVIRONMENT_INITIALIZERs, which
    // register the interceptor `provideSessionRecovery()` provides — to run, the
    // way `auth.interceptor.spec.ts` forces it by injecting `Router`.
    const recovery = TestBed.inject(SessionRecovery);
    const navigateSpy = vi
      .spyOn(recovery as unknown as { navigate(url: string): void }, 'navigate')
      .mockImplementation(() => undefined);

    stub = stubRunnerClient({
      'GET /api/auth/session': () => ({ auth_enabled: true, username: null, superuser: false }),
      'GET /runners/{runner_id}/api/leases': () =>
        stubError(401, { detail: 'not authenticated' }),
    });

    await runnerApi.listLeasesRunnersRunnerIdApiLeasesGet({
      path: { runner_id: 'rn_1' },
      throwOnError: false,
    });

    expect(navigateSpy).toHaveBeenCalledTimes(1);
  });
});
