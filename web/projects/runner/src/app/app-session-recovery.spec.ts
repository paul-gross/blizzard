import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { EVENT_SOURCE_FACTORY, type EventSourceFactory, type FleetEventSource, runnerApi, runnerClient } from 'fleet';
import { settle, stubError, stubRequestClient } from 'fleet/testing';
import { SessionRecovery } from 'local-panel';
import { vi } from 'vitest';

import { App } from './app';
import { routes } from './app.routes';

/** A do-nothing EventSource so `RunnerLiveUpdates` (blizzard#317 Phase 4) can open
 * without a real stream — mirrors `app.spec.ts`'s own `FakeEventSource`. */
class FakeEventSource {
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  addEventListener(): void {
    /* no-op: no case here drives the stream */
  }
  close(): void {
    /* no-op */
  }
}

/** Registers a fresh `SessionRecovery`'s interceptor, spying its navigation so no
 * case actually leaves jsdom — see `local-panel`'s own `session-recovery.spec.ts`
 * for why each case owns (and ejects) its own registration. */
async function setUp(route: (method: string, path: string) => unknown) {
  const factory: EventSourceFactory = () => new FakeEventSource() as unknown as FleetEventSource;
  await TestBed.configureTestingModule({
    imports: [App],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      provideRouter(routes),
      { provide: EVENT_SOURCE_FACTORY, useValue: factory },
    ],
  }).compileComponents();
  const recovery = TestBed.inject(SessionRecovery);
  const navigateSpy = vi
    .spyOn(recovery as unknown as { navigate(url: string): void }, 'navigate')
    .mockImplementation(() => undefined);
  const interceptorId = runnerClient.interceptors.response.use((response, request) => recovery.handle(response, request));
  const stub = stubRequestClient(runnerClient, route);
  return {
    recovery,
    navigateSpy,
    // blizzard#347: `async`, not because this teardown itself awaits anything,
    // but so `await`-ing it (below, and in `afterEach`) always gives the event
    // loop a turn before `sessionStorage.clear()` runs — the turn a 401 this
    // test never explicitly awaited (a concurrent query's own interceptor
    // call, the shape `App` mounting fires several of) needs to land its
    // `setMark()` here, against this test's own state, rather than after,
    // against the *next* test's clean slate instead.
    restore: async () => {
      runnerClient.interceptors.response.eject(interceptorId);
      stub.restore();
    },
  };
}

describe('runner App session-recovery fork (issue #312)', () => {
  let restore: (() => Promise<void>) | undefined;

  afterEach(async () => {
    await restore?.();
    restore = undefined;
    sessionStorage.clear();
  });

  it('renders local-panel through an upstream 401 whose session resolves a username', async () => {
    const { restore: r } = await setUp((_method, path) =>
      path === '/api/auth/session' ? { auth_enabled: true, username: 'alice' } : stubError(401, { detail: 'upstream' }),
    );
    restore = r;

    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('local-panel')).toBeTruthy();
    expect(el.querySelector('[data-testid="session-recovery"]')).toBeNull();
  });

  it('renders the recovery view instead of local-panel once the seam sets recovering', async () => {
    const { recovery, restore: r } = await setUp((_method, path) =>
      path === '/api/auth/session' ? { auth_enabled: true, username: null } : stubError(401, { detail: 'no session' }),
    );
    restore = r;

    // First no-session 401: mark set, the (mocked) bounce is "attempted".
    await runnerApi.listLeasesApiLeasesGet({ throwOnError: false });
    expect(recovery.recovering()).toBe(false);
    // A further no-session 401 while the mark still stands: sets `recovering`.
    await runnerApi.listLeasesApiLeasesGet({ throwOnError: false });
    expect(recovery.recovering()).toBe(true);

    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="session-recovery"]')).toBeTruthy();
    expect(el.querySelector('local-panel')).toBeNull();
  });

  it('re-attempts the bounce when the recovery view is retried', async () => {
    const { recovery, navigateSpy, restore: r } = await setUp((_method, path) =>
      path === '/api/auth/session' ? { auth_enabled: true, username: null } : stubError(401, { detail: 'no session' }),
    );
    restore = r;

    await runnerApi.listLeasesApiLeasesGet({ throwOnError: false });
    await runnerApi.listLeasesApiLeasesGet({ throwOnError: false });
    expect(recovery.recovering()).toBe(true);
    expect(navigateSpy).toHaveBeenCalledTimes(1);

    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    el.querySelector<HTMLButtonElement>('[data-testid="session-recovery-retry"]')?.click();
    await fixture.whenStable();

    expect(navigateSpy).toHaveBeenCalledTimes(2);
  });

  it('drains a recovery genuinely still in flight when teardown runs, before sessionStorage clears (blizzard#347)', async () => {
    // A session read under this test's own control — pending until this test
    // resolves it, so the recovery it drives is provably still in flight, not
    // just probably fast enough to have finished already.
    let resolveSession!: () => void;
    const pendingSession = new Promise<Response>((resolve) => {
      resolveSession = () =>
        resolve(
          new Response(JSON.stringify({ auth_enabled: true, username: null }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        );
    });

    const factory: EventSourceFactory = () => new FakeEventSource() as unknown as FleetEventSource;
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes),
        { provide: EVENT_SOURCE_FACTORY, useValue: factory },
      ],
    }).compileComponents();
    const recovery = TestBed.inject(SessionRecovery);
    vi.spyOn(recovery as unknown as { navigate(url: string): void }, 'navigate').mockImplementation(() => undefined);
    const interceptorId = runnerClient.interceptors.response.use((response, request) => recovery.handle(response, request));
    const previousConfig = runnerClient.getConfig();
    runnerClient.setConfig({
      baseUrl: 'http://localhost',
      fetch: (async (input: Request) => {
        const path = new URL(input.url).pathname;
        if (path === '/api/auth/session') return pendingSession;
        return new Response(JSON.stringify({ detail: 'not authenticated' }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        });
      }) as typeof fetch,
    });
    restore = async () => {
      runnerClient.interceptors.response.eject(interceptorId);
      runnerClient.setConfig(previousConfig);
    };

    // Fire-and-forget — the shape a concurrent query's own interceptor call
    // takes, never awaited by the test body itself.
    void runnerApi.listLeasesApiLeasesGet({ throwOnError: false });

    resolveSession();
    // Exercised directly (not just via `afterEach`), mirroring exactly what
    // teardown does. False if this is called without `await` (or `afterEach`
    // reverts to the same): a synchronous teardown gives the now-unblocked
    // recovery no turn to run before this assertion, the same gap that lets a
    // still-settling `setMark()` leak into a sibling spec's clean `sessionStorage`.
    await restore();
    restore = undefined;

    expect(sessionStorage.getItem('blizzard.runner.session-renewal-attempted')).not.toBeNull();
  });
});
