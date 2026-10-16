import { DOCUMENT } from '@angular/common';
import { DestroyRef, Injectable, InjectionToken, type Signal, inject, signal } from '@angular/core';

/**
 * The one owner of the browser's wall clock — a reading in epoch milliseconds.
 * Defaults to the real `Date.now`; a spec provides a stub to pin or advance time.
 * A one-shot reading (an event stamp, a single query window) injects this and
 * calls it; a display that must advance on its own reads {@link injectNowSignal}
 * instead, which ticks off the same clock. Nothing outside this directory reads
 * `Date.now()` or a zero-arg `new Date()` (`bzh:frontend-formatters`).
 */
export const FLEET_CLOCK = new InjectionToken<() => number>('FLEET_CLOCK', {
  providedIn: 'root',
  factory: () => Date.now,
});

/**
 * The one shared one-second clock behind every {@link injectNowSignal} reader.
 * Root-provided and started lazily by the first injection: a single interval
 * reads {@link FLEET_CLOCK} each second, so a page holds exactly one timer
 * however many displays read it. It pauses while the document is hidden and
 * re-reads immediately on becoming visible; the interval and listener are torn
 * down on the root injector's `DestroyRef`.
 */
@Injectable({ providedIn: 'root' })
export class FleetSecondClock {
  private readonly clock = inject(FLEET_CLOCK);
  private readonly document = inject(DOCUMENT);
  private readonly writable = signal(this.clock());
  private interval: ReturnType<typeof setInterval> | undefined;

  readonly now: Signal<number> = this.writable.asReadonly();

  constructor() {
    const onVisibility = (): void => {
      if (this.document.visibilityState === 'hidden') {
        this.stop();
        return;
      }
      this.writable.set(this.clock());
      this.start();
    };
    this.document.addEventListener('visibilitychange', onVisibility);
    inject(DestroyRef).onDestroy(() => {
      this.stop();
      this.document.removeEventListener('visibilitychange', onVisibility);
    });
    if (this.document.visibilityState !== 'hidden') {
      this.start();
    }
  }

  private start(): void {
    this.interval ??= setInterval(() => this.writable.set(this.clock()), 1000);
  }

  private stop(): void {
    clearInterval(this.interval);
    this.interval = undefined;
  }
}

/**
 * The shared, self-advancing {@link FLEET_CLOCK} signal — the one construct a
 * display that must advance on its own reads instead of calling `Date.now()`
 * inside a `computed()`. That call is untracked: a `computed()` only recomputes
 * when an *input* signal changes, so a heartbeat bar or an "Ns ago" label built
 * that way moves only when fresh data arrives and otherwise sits frozen between
 * polls. Reading this signal instead makes the same `computed()` recompute on
 * the tick *and* on incoming data, with no extra reset logic.
 *
 * Every caller reads the same one-second, visibility-paused clock
 * ({@link FleetSecondClock}); no caller creates a timer or chooses a cadence. A
 * consumer that needs a coarser value derives it in a `computed()` so string or
 * primitive equality stops the tick there. Like every other `injectXxx` helper
 * in this library, it must be called from an injection context.
 */
export function injectNowSignal(): Signal<number> {
  return inject(FleetSecondClock).now;
}
