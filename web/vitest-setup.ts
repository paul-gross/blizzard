import { afterEach, beforeEach } from 'vitest';

/**
 * Per-test `localStorage` reset, layered into every project through
 * `vitest-runner.config.mts`'s `setupFiles`.
 *
 * `@angular/build:unit-test` runs vitest with `isolate: false` — its own default,
 * chosen to match the Karma/Jasmine experience — so every spec file assigned to a
 * worker shares one jsdom, and therefore one `localStorage`. Anything a spec
 * persists there outlives the spec, the file, and the `TestBed` reset, and is read
 * back by the next file's first service construction.
 *
 * Two services read persisted state at construction: `ViewportService`
 * (`blizzard.viewport.override`) and `LoginPage` (the last provider). A spec that
 * pins a viewport leaves the next file's `ViewportService` constructing in that
 * mode, which silently re-decides `matchesMobileViewport` and hands `/board` the
 * mobile shell — a failure that reads as a broken route, lands in whichever file
 * the worker happened to schedule next, and only appears at the worker counts CI
 * runs at. Clearing around every test makes `localStorage` a per-test fixture, so
 * no spec can depend on — or leak into — another's.
 *
 * `sessionStorage` is deliberately left alone: it carries per-tab navigation state
 * (`auth-redirect.ts`, the runner's session-renewal mark), and the specs that
 * exercise it own its lifecycle themselves, including asserting on it across a
 * teardown they drive.
 */
beforeEach(() => localStorage.clear());

afterEach(() => localStorage.clear());

/**
 * jsdom implements neither `matchMedia`, `ResizeObserver`, nor `scrollIntoView`, which the hub's
 * scroll-selection-into-view reads on every selection. Inert stand-ins, installed only where jsdom
 * lacks them, let any page that selects a chunk render; a spec that asserts the reveal spies on them.
 */
if (typeof window !== 'undefined') {
  window.matchMedia ??= (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }) as MediaQueryList;
  const inert = (): void => undefined;
  globalThis.ResizeObserver ??= class {
    observe = inert;
    unobserve = inert;
    disconnect = inert;
  };
  Element.prototype.scrollIntoView ??= inert;
}
