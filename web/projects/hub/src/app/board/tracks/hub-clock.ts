import { type Signal, computed, effect, inject, signal, untracked } from '@angular/core';

import { FLEET_CLOCK } from 'fleet';
import { injectNowSignal } from 'fleet/shell';

/**
 * The hub's now, ticking on between its answers: the browser's ticking clock, shifted by how far the hub's clock ran
 * ahead of it when it last answered. `answeredAt` is the hub's now as its latest answer stamped it — `0` before any
 * answer, which leaves the browser's clock unshifted.
 */
export function injectHubClock(answeredAt: () => number): Signal<number> {
  const clock = inject(FLEET_CLOCK);
  const tick = injectNowSignal();
  const skew = signal(0);
  effect(() => {
    const latest = answeredAt();
    if (latest > 0) untracked(() => skew.set(latest - clock()));
  });
  return computed(() => tick() + skew());
}
