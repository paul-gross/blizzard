import { DestroyRef, type Signal, inject, signal } from '@angular/core';

/**
 * A self-ticking `Date.now()` signal (issue #178) — the one construct a display
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
  const now = signal(Date.now());
  const interval = setInterval(() => now.set(Date.now()), periodMs);
  inject(DestroyRef).onDestroy(() => clearInterval(interval));
  return now.asReadonly();
}
