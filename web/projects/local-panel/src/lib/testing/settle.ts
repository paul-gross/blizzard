import type { ComponentFixture } from '@angular/core/testing';

/**
 * Pump the fixture until an async read settles. TanStack Query resolves its
 * `queryFn` on a microtask that `whenStable()` alone doesn't always await under
 * zoneless change detection, so we interleave stability waits, a macrotask, and
 * a manual `detectChanges()` a handful of times to let the signal propagate and
 * the DOM re-render. Deterministic (the stubbed fetch resolves immediately).
 * Mirrors `fleet`'s `testing/settle.ts` — not exported from `fleet`'s public API,
 * so `local-panel` keeps its own copy rather than reaching into `fleet`'s internals.
 */
export async function settle(fixture: ComponentFixture<unknown>, tries = 8): Promise<void> {
  for (let i = 0; i < tries; i += 1) {
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  fixture.detectChanges();
}
