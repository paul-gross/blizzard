/**
 * Demo mode's waiting primitives.
 *
 * Every wait in the director is (a) abortable, so tearing the demo down never
 * leaves a timer running against a dead page, and (b) paced against the **wall
 * clock** rather than against accumulated timer callbacks. The second point is
 * what keeps a kiosk honest: a browser throttles timers and stops
 * `requestAnimationFrame` outright in a backgrounded or occluded tab, so a
 * cycle built by summing `setTimeout`s drifts arbitrarily far behind real time.
 * Deadlines here are absolute `performance.now()` instants, so a throttled
 * stretch resumes at the right place instead of accumulating lag.
 */

/** Thrown out of every wait once the run is torn down. */
export class DemoAborted extends Error {
  constructor() {
    super('demo aborted');
    this.name = 'DemoAborted';
  }
}

function throwIfAborted(signal: AbortSignal): void {
  if (signal.aborted) throw new DemoAborted();
}

/** Sleep `ms`, resolving early — by rejection — if the run is torn down. */
export function sleep(ms: number, signal: AbortSignal): Promise<void> {
  throwIfAborted(signal);
  if (ms <= 0) return Promise.resolve();
  return new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DemoAborted());
    };
    signal.addEventListener('abort', onAbort, { once: true });
  });
}

/** Sleep until an absolute `performance.now()` deadline. */
export function sleepUntil(deadline: number, signal: AbortSignal): Promise<void> {
  return sleep(deadline - performance.now(), signal);
}

/**
 * Wait for the next paint, or ~250ms, whichever comes first.
 *
 * The race is the point: `requestAnimationFrame` alone never fires in a
 * background tab, which would freeze a scroll animation indefinitely rather
 * than let it finish coarsely. The timeout arm keeps the animation progressing
 * (jerkily, unobserved) so it still completes on schedule.
 */
export function nextFrame(signal: AbortSignal): Promise<void> {
  throwIfAborted(signal);
  return new Promise<void>((resolve, reject) => {
    let settled = false;
    const done = () => {
      if (settled) return;
      settled = true;
      cancelAnimationFrame(frame);
      clearTimeout(timer);
      signal.removeEventListener('abort', onAbort);
      resolve();
    };
    const onAbort = () => {
      if (settled) return;
      settled = true;
      cancelAnimationFrame(frame);
      clearTimeout(timer);
      reject(new DemoAborted());
    };
    const frame = requestAnimationFrame(() => done());
    const timer = setTimeout(done, 250);
    signal.addEventListener('abort', onAbort, { once: true });
  });
}

/**
 * Poll `read` each frame until it answers something other than `null`, giving
 * up after `timeoutMs` and answering `null`.
 *
 * A timeout is a *result*, not a failure: a chunk whose detail never resolves
 * (a hub blip, a chunk that vanished) should cost the demo one dwell, not stop
 * the tour. Callers degrade — skip the scroll, swap early — rather than throw.
 */
export async function waitFor<T>(
  read: () => T | null,
  timeoutMs: number,
  signal: AbortSignal,
): Promise<T | null> {
  const deadline = performance.now() + timeoutMs;
  for (;;) {
    const value = read();
    if (value !== null) return value;
    if (performance.now() >= deadline) return null;
    await nextFrame(signal);
  }
}
