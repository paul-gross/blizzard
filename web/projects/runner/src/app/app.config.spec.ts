import { TestBed } from '@angular/core/testing';
import { runnerApi, runnerClient } from 'fleet';
import { type RequestClientStub, stubError, stubRequestClient } from 'fleet/testing';
import { SessionRecovery } from 'local-panel';
import { vi } from 'vitest';

import { appConfig } from './app.config';

/**
 * Proves `provideSessionRecovery()` is actually reachable through the runner
 * app's own production provider list (issue #312) — every other
 * `SessionRecovery` spec assembles its own providers (`session-recovery.spec.ts`),
 * which would stay green even if `app.config.ts` forgot to call
 * `provideSessionRecovery()`. `blizzard:e2e` gates no PR and no push
 * (`bzh:gating-tier-pins-production-paths`), so this is the one `web:unit-test`
 * asserting against `appConfig.providers` itself rather than a hand-assembled list.
 */
describe('runner appConfig wires session recovery (issue #312)', () => {
  let stub: RequestClientStub;

  beforeEach(() => {
    // blizzard#347: this project's specs share one jsdom/module registry under
    // single-worker scheduling (Angular's unit-test builder defaults Vitest to
    // `isolate: false`), so a sibling spec's leaked renewal mark could otherwise
    // become this test's starting state. Seed the exact dirty state a leak
    // leaves — the key `session-recovery.ts` sets (hardcoded, not imported: it
    // is a private module constant) — so the hook below is proven to establish
    // a clean precondition rather than merely inherit whatever came before it.
    sessionStorage.setItem('blizzard.runner.session-renewal-attempted', '1');
  });

  beforeEach(() => {
    sessionStorage.clear();
  });

  afterEach(() => {
    stub.restore();
    runnerClient.interceptors.response.clear();
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

    stub = stubRequestClient(runnerClient, (_method, path) =>
      path === '/api/auth/session' ? { auth_enabled: true, username: null } : stubError(401, { detail: 'not authenticated' }),
    );

    await runnerApi.listLeasesApiLeasesGet({ throwOnError: false });

    expect(navigateSpy).toHaveBeenCalledTimes(1);
  });
});
