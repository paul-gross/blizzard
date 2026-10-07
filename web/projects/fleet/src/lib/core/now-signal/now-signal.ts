import { DestroyRef, InjectionToken, type Signal, inject, signal } from '@angular/core';

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
 * A self-ticking {@link FLEET_CLOCK} signal — the one construct a display
 * that must advance on its own reads instead of calling `Date.now()` inside a
 * `computed()`. That call is untracked: a `computed()` only recomputes when an
 * *input* signal changes, so a heartbeat bar or an "Ns ago" label built that way
 * moves only when fresh data arrives and otherwise sits frozen between polls.
 * Reading {@link injectNowSignal}'s signal instead makes the same `computed()`
 * recompute on the tick *and* on incoming data, with no extra reset logic.
 *
 * The interval is cleared on the calling context's `DestroyRef` teardown
 * (`fleet-live.ts`'s SSE-reconnect teardown is the same shape), so a destroyed
 * host leaves nothing running. Like every other `injectXxx` helper in this
 * library, it must be called from an injection context — a component or
 * directive field initializer, or inside `runInInjectionContext`.
 */
export function injectNowSignal(periodMs: number): Signal<number> {
  const clock = inject(FLEET_CLOCK);
  const now = signal(clock());
  const interval = setInterval(() => now.set(clock()), periodMs);
  inject(DestroyRef).onDestroy(() => clearInterval(interval));
  return now.asReadonly();
}
