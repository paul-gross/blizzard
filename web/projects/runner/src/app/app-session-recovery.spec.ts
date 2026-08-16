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
    restore: () => {
      runnerClient.interceptors.response.eject(interceptorId);
      stub.restore();
    },
  };
}

describe('runner App session-recovery fork (issue #312)', () => {
  let restore: (() => void) | undefined;

  afterEach(() => {
    restore?.();
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
});
