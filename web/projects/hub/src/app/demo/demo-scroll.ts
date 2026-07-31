import { nextFrame } from './demo-timing';

/**
 * Demo mode's slow scroll — the thing that makes an unattended board readable
 * rather than a static screenshot.
 *
 * The director never names a scroll container by class. It hands in a region
 * (the board's detail dock, the artifact viewer) and {@link scrollersIn}
 * discovers which elements inside it actually scroll, so a layout change in
 * `fleet`'s panes — three dock columns today, some other arrangement tomorrow —
 * doesn't leave the demo silently scrolling nothing. Discovery is by computed
 * `overflow-y` plus real overflow, which is exactly the condition under which
 * scrolling would do something.
 */

/** How much overflow is worth animating — below this a "scroll" is a jitter. */
const MIN_OVERFLOW_PX = 8;

/** Every element in `root` (itself included) that both scrolls and has somewhere to scroll. */
export function scrollersIn(root: HTMLElement): HTMLElement[] {
  const found: HTMLElement[] = [];
  const consider = (el: HTMLElement): void => {
    const overflowY = getComputedStyle(el).overflowY;
    if (overflowY !== 'auto' && overflowY !== 'scroll') return;
    if (el.scrollHeight - el.clientHeight < MIN_OVERFLOW_PX) return;
    found.push(el);
  };

  consider(root);
  for (const el of root.querySelectorAll<HTMLElement>('*')) consider(el);
  return found;
}

/**
 * Ease every element from where it sits to its bottom over `durationMs`, all in
 * step — the dock's three columns descend together rather than one after
 * another.
 *
 * Paced off `performance.now()` against an absolute deadline (see
 * `demo-timing.ts`), and each element's travel is re-measured every frame: the
 * board is live, so an SSE update that appends a timeline row mid-scroll
 * lengthens the target instead of stranding the pane short of the new bottom.
 *
 * `ease` is a sine ease-in-out over the *whole* span, so the scroll starts and
 * ends imperceptibly and never reads as a jump-cut on a wall screen.
 */
export async function slowScrollToBottom(
  targets: readonly HTMLElement[],
  durationMs: number,
  signal: AbortSignal,
): Promise<void> {
  if (targets.length === 0 || durationMs <= 0) return;

  const starts = targets.map((el) => el.scrollTop);
  const startedAt = performance.now();

  for (;;) {
    const elapsed = performance.now() - startedAt;
    const progress = Math.min(1, elapsed / durationMs);
    const eased = (1 - Math.cos(Math.PI * progress)) / 2;

    targets.forEach((el, index) => {
      const bottom = Math.max(0, el.scrollHeight - el.clientHeight);
      const from = Math.min(starts[index], bottom);
      el.scrollTop = from + (bottom - from) * eased;
    });

    if (progress >= 1) return;
    await nextFrame(signal);
  }
}
