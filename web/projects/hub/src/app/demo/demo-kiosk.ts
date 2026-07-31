import { Injectable, InjectionToken, inject } from '@angular/core';

/**
 * The kiosk half of demo mode — the two things a board left on a wall screen
 * for days needs that a board watched for ten minutes does not.
 *
 * **1. The screen has to stay on.** A TV browser blanks the display on its own
 * idle timer, and a page that never receives input looks idle no matter how
 * much it animates. The Screen Wake Lock API is the only thing that actually
 * suppresses that, and the lock is *released for you* whenever the tab is
 * hidden or the device sleeps — so it is re-acquired on every
 * `visibilitychange` back to visible, not just once at start. Feature-detected:
 * the API needs a secure context, so a kiosk served over plain HTTP simply goes
 * without rather than throwing.
 *
 * **2. The page has to pick up a redeploy.** This is the failure that makes
 * long-lived kiosks go stale: an SPA is fetched once and then runs its original
 * bundle forever. A new hub deploy changes the hashed bundle names inside
 * `index.html`, but nothing re-reads `index.html` after boot, so a screen can
 * sit for weeks on code that no longer exists on the server — and hard-refresh
 * schemes built on `location.reload()` timers alone still serve the *cached*
 * document. So: snapshot `index.html` at boot with `cache: 'no-store'`,
 * re-fetch it at each swap boundary, and reload when the bytes differ. The
 * `no-store` is load-bearing — without it the re-fetch is answered from the
 * HTTP cache and the check can never fire.
 *
 * A plain uptime backstop sits under that ({@link DemoConfig.maxUptimeMs}), for
 * the leaks and zombie SSE connections that no deploy will fix.
 *
 * Both reload paths fire only at a **swap boundary** — between chunks, never
 * mid-scroll — so a refresh reads as the next slide rather than a glitch. The
 * demo params ride the URL (`demo-config.ts`), so what comes back up is the
 * demo, not a plain board.
 */
@Injectable({ providedIn: 'root' })
export class DemoKiosk {
  private readonly env = inject(DEMO_KIOSK_ENV);

  /** The document as it read at boot, or `null` if that first read failed —
   * in which case the deploy check stays disarmed rather than guessing. */
  private stamp: string | null = null;

  private bootAt = 0;

  private wakeLock: WakeLockRelease | null = null;

  private released = false;

  private started = false;

  /** Take the wake lock and snapshot the deployed document. Idempotent. */
  async begin(): Promise<void> {
    if (this.started) return;
    this.started = true;
    this.released = false;
    this.bootAt = this.env.now();

    globalThis.document.addEventListener('visibilitychange', this.onVisible);
    await this.acquireWakeLock();
    this.stamp = await this.readDeployStamp();
  }

  /**
   * Give the screen back: drop the wake lock and stop re-taking it.
   *
   * The counterpart to {@link begin}, called from `DemoDirector.stop()`. Without
   * it a stopped demo still pins a display awake for the life of the tab, and
   * `started` stays latched so the tour cannot be restarted cleanly — the lock
   * is the one piece of this class that outlives its own loop.
   */
  async end(): Promise<void> {
    this.released = true;
    this.started = false;
    globalThis.document.removeEventListener('visibilitychange', this.onVisible);
    const lock = this.wakeLock;
    this.wakeLock = null;
    await lock?.release().catch(() => undefined);
  }

  /** Bound once so `removeEventListener` in {@link end} matches the registration. */
  private readonly onVisible = (): void => {
    if (globalThis.document.visibilityState === 'visible') void this.acquireWakeLock();
  };

  /**
   * Reload the page if the deploy moved under it, or if it has been up past
   * `maxUptimeMs`. Answers whether a reload was triggered — the caller is
   * expected to stop driving, since the document is on its way out.
   */
  async reloadIfStale(maxUptimeMs: number): Promise<boolean> {
    if (maxUptimeMs > 0 && this.env.now() - this.bootAt >= maxUptimeMs) return this.reload();

    const current = await this.readDeployStamp();
    // A failed re-read is a network blip, not a redeploy — reloading on it would
    // turn every hiccup into a blank screen while the hub is unreachable.
    if (this.stamp === null || current === null || current === this.stamp) return false;
    return this.reload();
  }

  private reload(): boolean {
    this.env.reload();
    return true;
  }

  /** The deployed `index.html`, read past every cache, or `null` if unreadable. */
  private async readDeployStamp(): Promise<string | null> {
    try {
      const response = await this.env.fetch('/index.html', { cache: 'no-store' });
      if (!response.ok) return null;
      return await response.text();
    } catch {
      return null;
    }
  }

  private async acquireWakeLock(): Promise<void> {
    if (this.released) return;
    const api = this.env.wakeLock();
    if (api === undefined) return;
    try {
      const lock = await api.request('screen');
      // `end()` can land while the request is in flight — honour it rather than
      // stashing a lock nothing will ever release.
      if (this.released) await lock.release().catch(() => undefined);
      else this.wakeLock = lock;
    } catch {
      // Denied (insecure context, unsupported display, policy) — the demo runs
      // regardless; the screen just keeps its own idle timer.
      this.wakeLock = null;
    }
  }
}

/** The slice of the Screen Wake Lock API this file uses — declared locally so the
 * app does not depend on the DOM lib shipping it. */
export interface WakeLockRelease {
  release(): Promise<void>;
}

export interface WakeLockApi {
  request(type: 'screen'): Promise<WakeLockRelease>;
}

/**
 * The ambient browser capabilities {@link DemoKiosk} drives, behind one seam.
 *
 * Reached through a token rather than off `globalThis` because all three are
 * untestable in place: a spec cannot let `location.reload()` run, cannot serve
 * `/index.html` to a bare `fetch` under jsdom, and cannot present a wake-lock
 * implementation the platform does not have. Without the seam the reload
 * decision — the one genuinely branchy thing in this feature — is unreachable
 * at the unit tier, and a demo screen that silently never picks up a redeploy
 * is exactly the failure this class exists to prevent.
 */
export interface DemoKioskEnv {
  fetch(input: string, init?: RequestInit): Promise<Response>;
  reload(): void;
  now(): number;
  wakeLock(): WakeLockApi | undefined;
}

/** The real browser, bound by default. Specs override this token. */
export const DEMO_KIOSK_ENV = new InjectionToken<DemoKioskEnv>('DEMO_KIOSK_ENV', {
  providedIn: 'root',
  factory: (): DemoKioskEnv => ({
    fetch: (input, init) => fetch(input, init),
    reload: () => globalThis.location.reload(),
    now: () => Date.now(),
    wakeLock: () => (globalThis.navigator as NavigatorWithWakeLock).wakeLock,
  }),
});

interface NavigatorWithWakeLock {
  wakeLock?: WakeLockApi;
}
