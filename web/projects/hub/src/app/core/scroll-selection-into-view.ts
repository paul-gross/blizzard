import {
  afterEveryRender,
  DestroyRef,
  effect,
  ElementRef,
  inject,
} from '@angular/core';

/** How long after the picked element renders a resize of its scroll container still re-reveals it — long enough for a
 * panel opening beside or below the list to settle. */
const SETTLE_MS = 350;

/**
 * Keep the picked item in view as a details panel opens: each time `selectedId` changes to a new chunk — and once on
 * load when it is already set — wait for `selector(id)` to render inside the host, then scroll it to the nearest edge
 * of its scroll container. The container is watched briefly afterwards, so a panel that takes room once the pick has
 * rendered re-reveals it. An item already in view does not move; reduced motion jumps rather than glides.
 */
export function injectScrollSelectionIntoView(
  selectedId: () => string | null,
  selector: (id: string) => string,
): void {
  const host = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  let last: string | null = null;
  let pending: string | null = null;
  let stop: (() => void) | null = null;

  effect(() => {
    const id = selectedId();
    if (id === last) return;
    last = id;
    pending = id;
  });

  afterEveryRender(() => {
    if (pending === null) return;
    const el = host.querySelector<HTMLElement>(selector(pending));
    if (!el) return;
    pending = null;
    stop?.();
    stop = reveal(el);
  });

  inject(DestroyRef).onDestroy(() => stop?.());
}

function reveal(el: HTMLElement): () => void {
  const behavior: ScrollBehavior = matchMedia(
    '(prefers-reduced-motion: reduce)',
  ).matches
    ? 'auto'
    : 'smooth';
  let frame = 0;
  const scroll = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() =>
      el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior }),
    );
  };
  const container = scrollParent(el);
  // A ResizeObserver reports once on observe, so the reveal runs right away and again as the panel takes its room.
  const observer = new ResizeObserver(scroll);
  observer.observe(container);
  const timer = setTimeout(() => observer.disconnect(), SETTLE_MS);
  return () => {
    observer.disconnect();
    clearTimeout(timer);
    cancelAnimationFrame(frame);
  };
}

function scrollParent(el: HTMLElement): HTMLElement {
  for (let node = el.parentElement; node; node = node.parentElement) {
    const { overflowX, overflowY } = getComputedStyle(node);
    if (/auto|scroll/.test(overflowY + overflowX)) return node;
  }
  return document.documentElement;
}
