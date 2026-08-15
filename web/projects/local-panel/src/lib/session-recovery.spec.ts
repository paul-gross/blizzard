import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideTanStackQuery, QueryClient } from '@tanstack/angular-query-experimental';
import { runnerApi, runnerClient } from 'fleet';
import { stubError, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { injectRunnerLogoutMutation } from './auth.query';
import { SessionRecovery } from './session-recovery';

type Restore = () => void;

function unauthorized() {
  return stubError(401, { detail: 'not authenticated' });
}

/**
 * Registers a fresh `SessionRecovery`'s interceptor on `runnerClient`, stubs the
 * transport with `route`, and spies its (protected) `navigate` so specs can assert
 * what it was called with instead of actually leaving jsdom — mirroring how
 * `local-identity.spec.ts` spies `reload`.
 *
 * `runnerClient` is a module-level singleton and `stubRequestClient`'s `restore()`
 * only resets its `fetch`, never the registered interceptor — so every case
 * registers (and the matching `afterEach` ejects) its own, and clears the
 * `sessionStorage` mark, to keep specs isolated.
 */
function setUp(route: (method: string, path: string) => unknown) {
  TestBed.configureTestingModule({
    providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
  });
  const recovery = TestBed.inject(SessionRecovery);
  const navigateSpy = vi
    .spyOn(recovery as unknown as { navigate(url: string): void }, 'navigate')
    .mockImplementation(() => undefined);
  const interceptorId = runnerClient.interceptors.response.use((response, request) => recovery.handle(response, request));
  const stub = stubRequestClient(runnerClient, route);
  return {
    recovery,
    navigateSpy,
    restore: () => {
      runnerClient.interceptors.response.eject(interceptorId);
      stub.restore();
    },
  };
}

describe('SessionRecovery (issue #312)', () => {
  let restore: Restore | undefined;

  afterEach(() => {
    restore?.();
    restore = undefined;
    sessionStorage.clear();
  });

  it('navigates to /api/auth/login with the live pathname+search on a no-session 401', async () => {
    history.pushState({}, '', '/?chunk=ch_1');
    const { navigateSpy, restore: r } = setUp((_method, path) =>
      path === '/api/auth/session' ? { auth_enabled: true, username: null } : unauthorized(),
    );
    restore = r;

    await runnerApi.listLeasesApiLeasesGet({ throwOnError: false });

    expect(navigateSpy).toHaveBeenCalledTimes(1);
    expect(navigateSpy).toHaveBeenCalledWith(`/api/auth/login?return_to=${encodeURIComponent('/?chunk=ch_1')}`);
  });

  it('coalesces ten concurrent no-session 401s into one navigation', async () => {
    history.pushState({}, '', '/?chunk=ch_2');
    const { navigateSpy, restore: r } = setUp((_method, path) =>
      path === '/api/auth/session' ? { auth_enabled: true, username: null } : unauthorized(),
    );
    restore = r;

    await Promise.all(Array.from({ length: 10 }, () => runnerApi.listLeasesApiLeasesGet({ throwOnError: false })));

    expect(navigateSpy).toHaveBeenCalledTimes(1);
  });

  it('navigates on a direct recoverFromUnauthenticated() call — the shape RunnerLiveUpdates drives on a stream 401 (D9)', async () => {
    history.pushState({}, '', '/?chunk=ch_live');
    const { recovery, navigateSpy, restore: r } = setUp((_method, path) =>
      path === '/api/auth/session' ? { auth_enabled: true, username: null } : unauthorized(),
    );
    restore = r;

    // No Response/Request at all — the stream's transport never produces either,
    // which is exactly why this logic had to be lifted out of `handle`'s body.
    await recovery.recoverFromUnauthenticated();

    expect(navigateSpy).toHaveBeenCalledTimes(1);
    expect(navigateSpy).toHaveBeenCalledWith(`/api/auth/login?return_to=${encodeURIComponent('/?chunk=ch_live')}`);
  });

  it('leaves a 401 untouched when the session read still resolves a username — an upstream rejection, not an expiry', async () => {
    const { navigateSpy, recovery, restore: r } = setUp((_method, path) =>
      path === '/api/auth/session' ? { auth_enabled: true, username: 'alice' } : unauthorized(),
    );
    restore = r;

    const result = await runnerApi.listLeasesApiLeasesGet({ throwOnError: false });

    expect(navigateSpy).not.toHaveBeenCalled();
    expect(recovery.recovering()).toBe(false);
    expect(result.response?.status).toBe(401);
  });

  it('leaves a 401 untouched on an authless surface (auth_enabled: false)', async () => {
    const { navigateSpy, recovery, restore: r } = setUp((_method, path) =>
      path === '/api/auth/session' ? { auth_enabled: false, username: null } : unauthorized(),
    );
    restore = r;

    const result = await runnerApi.listLeasesApiLeasesGet({ throwOnError: false });

    expect(navigateSpy).not.toHaveBeenCalled();
    expect(recovery.recovering()).toBe(false);
    expect(result.response?.status).toBe(401);
  });

  it('acts on neither a 403 nor a success', async () => {
    const { navigateSpy, restore: r } = setUp((_method, path) => {
      if (path === '/api/auth/session') return { auth_enabled: true, username: null };
      if (path === '/api/leases') return stubError(403, { detail: 'forbidden' });
      return {};
    });
    restore = r;

    await runnerApi.listLeasesApiLeasesGet({ throwOnError: false });
    await runnerApi.readSessionApiAuthSessionGet({ throwOnError: false });

    expect(navigateSpy).not.toHaveBeenCalled();
  });

  it('does not navigate again while the mark from a prior attempt still stands, and sets recovering instead', async () => {
    history.pushState({}, '', '/?chunk=ch_3');
    const first = setUp((_method, path) =>
      path === '/api/auth/session' ? { auth_enabled: true, username: null } : unauthorized(),
    );

    // The initial attempt: mark set, one navigation.
    await runnerApi.listLeasesApiLeasesGet({ throwOnError: false });
    expect(first.navigateSpy).toHaveBeenCalledTimes(1);
    first.restore();

    // "app re-created": a fresh instance and interceptor, the way a real full-page
    // navigation would tear down and rebuild the whole app — but `sessionStorage`
    // (a document-wide store, not component state) survives across it, which is
    // the mark's whole point.
    TestBed.resetTestingModule();
    const second = setUp((_method, path) =>
      path === '/api/auth/session' ? { auth_enabled: true, username: null } : unauthorized(),
    );
    restore = second.restore;

    // An ordinary session poll resolving no username along the way must not clear
    // the mark — only a resolved username does (D4).
    await runnerApi.readSessionApiAuthSessionGet({ throwOnError: false });

    // A further no-session 401: the mark still stands, so this sets `recovering`
    // rather than firing a second navigation.
    await runnerApi.listLeasesApiLeasesGet({ throwOnError: false });

    expect(second.navigateSpy).not.toHaveBeenCalled();
    expect(second.recovery.recovering()).toBe(true);
  });

  it('clears the mark and recovering state only on a session read that resolves a username', async () => {
    history.pushState({}, '', '/?chunk=ch_4');
    let sessionBody: unknown = { auth_enabled: true, username: null };
    const { navigateSpy, recovery, restore: r } = setUp((_method, path) =>
      path === '/api/auth/session' ? sessionBody : unauthorized(),
    );
    restore = r;

    await runnerApi.listLeasesApiLeasesGet({ throwOnError: false });
    expect(navigateSpy).toHaveBeenCalledTimes(1);

    sessionBody = { auth_enabled: true, username: 'alice' };
    await runnerApi.readSessionApiAuthSessionGet({ throwOnError: false });
    expect(recovery.recovering()).toBe(false);

    // The mark itself is now clear too — proven by a fresh no-session 401 firing a
    // second, independent navigation rather than setting `recovering`.
    sessionBody = { auth_enabled: true, username: null };
    await runnerApi.listLeasesApiLeasesGet({ throwOnError: false });
    expect(navigateSpy).toHaveBeenCalledTimes(2);
    expect(recovery.recovering()).toBe(false);
  });

  it('does not navigate on a 401 while a logout is in flight', async () => {
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const recovery = TestBed.inject(SessionRecovery);
    const navigateSpy = vi
      .spyOn(recovery as unknown as { navigate(url: string): void }, 'navigate')
      .mockImplementation(() => undefined);
    const interceptorId = runnerClient.interceptors.response.use((response, request) => recovery.handle(response, request));

    let resolveLogout!: () => void;
    const pendingLogout = new Promise<Response>((resolve) => {
      resolveLogout = () => resolve(new Response(null, { status: 204 }));
    });
    const previousConfig = runnerClient.getConfig();
    runnerClient.setConfig({
      baseUrl: 'http://localhost',
      fetch: (async (input: Request) => {
        const path = new URL(input.url).pathname;
        if (path === '/api/auth/logout') return pendingLogout;
        if (path === '/api/auth/session') {
          return new Response(JSON.stringify({ auth_enabled: true, username: null }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          });
        }
        return new Response(JSON.stringify({ detail: 'not authenticated' }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        });
      }) as typeof fetch,
    });
    restore = () => {
      runnerClient.interceptors.response.eject(interceptorId);
      runnerClient.setConfig(previousConfig);
    };

    const logout = TestBed.runInInjectionContext(() => injectRunnerLogoutMutation());
    const logoutPromise = logout.mutateAsync();
    // Let `onMutate`'s synchronous flag flip land before probing — `mutate` itself
    // does not await it.
    await Promise.resolve();

    await runnerApi.listLeasesApiLeasesGet({ throwOnError: false });
    expect(navigateSpy).not.toHaveBeenCalled();

    resolveLogout();
    await logoutPromise;
  });
});
