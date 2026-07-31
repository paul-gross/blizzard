import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { DEMO_KIOSK_ENV, type DemoKioskEnv, DemoKiosk, type WakeLockApi, type WakeLockRelease } from './demo-kiosk';

/**
 * The kiosk's reload decision — the branchiest thing in demo mode, and the one
 * whose failures are both invisible and severe: a wall screen that silently
 * never picks up a redeploy, or one that reloads on every network hiccup.
 *
 * Driven through {@link DEMO_KIOSK_ENV}, the seam that exists precisely so this
 * is reachable: a spec cannot let `location.reload()` run, cannot serve
 * `/index.html` to a bare `fetch` under jsdom, and cannot conjure a wake-lock
 * implementation the platform lacks.
 */
describe('DemoKiosk', () => {
  /** A scriptable browser: `documents` is the queue `/index.html` answers with. */
  function harness(documents: (string | Error)[], wakeLock?: WakeLockApi) {
    const state = { reloads: 0, now: 0, fetched: [] as (RequestInit | undefined)[] };
    const env: DemoKioskEnv = {
      fetch: (_input, init) => {
        state.fetched.push(init);
        const next = documents.shift();
        if (next instanceof Error) return Promise.reject(next);
        if (next === undefined) return Promise.reject(new Error('no scripted response'));
        return Promise.resolve(new Response(next, { status: 200 }));
      },
      reload: () => {
        state.reloads += 1;
      },
      now: () => state.now,
      wakeLock: () => wakeLock,
    };
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), { provide: DEMO_KIOSK_ENV, useValue: env }],
    });
    return { state, kiosk: () => TestBed.inject(DemoKiosk) };
  }

  const PAGE = '<script src="chunk-AAAA.js">';
  const REDEPLOYED = '<script src="chunk-BBBB.js">';

  it('reloads when the deployed document changed under it', async () => {
    const { state, kiosk } = harness([PAGE, REDEPLOYED]);
    const k = kiosk();
    await k.begin();

    expect(await k.reloadIfStale(0)).toBe(true);
    expect(state.reloads).toBe(1);
  });

  it('sits still while the deployed document is unchanged', async () => {
    const { state, kiosk } = harness([PAGE, PAGE]);
    const k = kiosk();
    await k.begin();

    expect(await k.reloadIfStale(0)).toBe(false);
    expect(state.reloads).toBe(0);
  });

  it('reads past the cache — without no-store the check could never fire', async () => {
    const { state, kiosk } = harness([PAGE, PAGE]);
    await kiosk().begin();

    expect(state.fetched[0]?.cache).toBe('no-store');
  });

  it('treats an unreadable re-read as a blip, not a redeploy', async () => {
    const { state, kiosk } = harness([PAGE, new Error('offline')]);
    const k = kiosk();
    await k.begin();

    expect(await k.reloadIfStale(0)).toBe(false);
    expect(state.reloads).toBe(0);
  });

  it('stays disarmed when the boot read itself failed, rather than guessing', async () => {
    const { state, kiosk } = harness([new Error('offline'), REDEPLOYED]);
    const k = kiosk();
    await k.begin();

    // No baseline to compare against — a difference here would be meaningless.
    expect(await k.reloadIfStale(0)).toBe(false);
    expect(state.reloads).toBe(0);
  });

  it('reloads on the uptime backstop even when the deploy never moved', async () => {
    const { state, kiosk } = harness([PAGE, PAGE]);
    const k = kiosk();
    await k.begin();
    state.now = 60 * 60 * 1000;

    expect(await k.reloadIfStale(60 * 60 * 1000)).toBe(true);
    expect(state.reloads).toBe(1);
  });

  it('never fires the backstop when it is disabled with 0', async () => {
    const { state, kiosk } = harness([PAGE, PAGE]);
    const k = kiosk();
    await k.begin();
    state.now = 365 * 24 * 60 * 60 * 1000;

    expect(await k.reloadIfStale(0)).toBe(false);
    expect(state.reloads).toBe(0);
  });

  it('does not fire the backstop before its time', async () => {
    const { state, kiosk } = harness([PAGE, PAGE]);
    const k = kiosk();
    await k.begin();
    state.now = 59 * 60 * 1000;

    expect(await k.reloadIfStale(60 * 60 * 1000)).toBe(false);
    expect(state.reloads).toBe(0);
  });

  describe('the wake lock', () => {
    function lockStub() {
      const released = { count: 0 };
      const lock: WakeLockRelease = {
        release: () => {
          released.count += 1;
          return Promise.resolve();
        },
      };
      return { released, api: { request: () => Promise.resolve(lock) } satisfies WakeLockApi };
    }

    it('is taken at the start and given back by end()', async () => {
      const { released, api } = lockStub();
      const { kiosk } = harness([PAGE], api);
      const k = kiosk();

      await k.begin();
      await k.end();

      expect(released.count).toBe(1);
    });

    it('is not re-taken after end(), so a stopped demo stops pinning the screen', async () => {
      const { released, api } = lockStub();
      const { kiosk } = harness([PAGE], api);
      const k = kiosk();
      await k.begin();
      await k.end();

      globalThis.document.dispatchEvent(new Event('visibilitychange'));
      await Promise.resolve();

      expect(released.count).toBe(1);
    });

    it('runs the demo anyway on a platform that has no wake lock', async () => {
      const { kiosk } = harness([PAGE], undefined);

      await expect(kiosk().begin()).resolves.toBeUndefined();
    });

    it('runs the demo anyway when the request is denied', async () => {
      const denied: WakeLockApi = { request: () => Promise.reject(new Error('insecure context')) };
      const { kiosk } = harness([PAGE], denied);

      await expect(kiosk().begin()).resolves.toBeUndefined();
    });
  });
});
