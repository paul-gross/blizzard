import { InjectionToken, type Signal, inject, signal } from '@angular/core';

/**
 * The project an app's reads are narrowed to — the hub board's project lens, as the slug its URL names, or `null` for
 * the whole tenant. A read the lens narrows sends it as `?project=` and carries it in its query key; the live stream
 * reopens on it.
 *
 * A neutral seam: the hub app provides it from its lens, and an app with no projects (the runner's) provides nothing,
 * so every read there stays whole.
 */
export const HUB_PROJECT_FILTER = new InjectionToken<Signal<string | null>>('fleet.HUB_PROJECT_FILTER', {
  providedIn: 'root',
  factory: () => signal(null).asReadonly(),
});

/** The project the reads are narrowed to now, `null` for the whole tenant — {@link HUB_PROJECT_FILTER}'s value. */
export function injectProjectFilter(): Signal<string | null> {
  return inject(HUB_PROJECT_FILTER);
}
