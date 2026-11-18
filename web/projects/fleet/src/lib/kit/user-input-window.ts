import { afterNextRender, DestroyRef, ElementRef, inject, Injector } from '@angular/core';

/**
 * Tells an Aria widget's own writes from a person's. Aria moves its selection inside the click/key
 * handler but publishes it in its next render's write phase, so a person's input is a window that
 * closes after that render — the read phase, which runs once every write-phase effect has.
 *
 * Call it in an injection context of the widget's host; the returned function reports whether the
 * window is open.
 */
export function trackUserInput(): () => boolean {
  const host = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  const injector = inject(Injector);
  let open = false;
  const begin = () => {
    open = true;
    afterNextRender({ read: () => (open = false) }, { injector });
  };
  for (const type of ['click', 'keydown']) host.addEventListener(type, begin, true);
  inject(DestroyRef).onDestroy(() => {
    for (const type of ['click', 'keydown']) host.removeEventListener(type, begin, true);
  });
  return () => open;
}
