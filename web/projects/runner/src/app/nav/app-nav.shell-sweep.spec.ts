import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { page } from 'vitest/browser';

import { AppNav } from './app-nav';

/**
 * The runner shell's top tab strip (issue #313) — its own half of
 * `web:shell-sweep` (`blizzard-context:/verification/blizzard.md`
 * bzh:web-shell-sweep). `KitTabStrip`/`KitTab`'s chrome is unclamped flex
 * (no `@container` rule of its own), so this is a narrower proof than the
 * header-menu sweeps: only that the two static `Board`/`Events` labels never
 * force the strip to overflow its own width, at every viewport from a wide
 * monitor down to the narrowest common phone.
 *
 * Excluded from the default `ng test runner` run (`angular.json`'s
 * `test.exclude`) because it needs `--browsers=ChromiumHeadless`, not jsdom —
 * run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const WIDTHS = [1400, 1024, 768, 640, 480, 390, 320];

describe('runner app-nav shell sweep (web:shell-sweep, issue #313)', () => {
  it('renders both tabs with no horizontal overflow at every width', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    await TestBed.configureTestingModule({
      imports: [AppNav],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const fixture = TestBed.createComponent(AppNav);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      for (const width of WIDTHS) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const label = `width=${width}`;
        const nav = root.querySelector<HTMLElement>('[data-testid="app-nav"]');
        expect(nav, `${label}: no tab strip in the DOM`).not.toBeNull();
        const board = root.querySelector<HTMLElement>('[data-testid="nav-board"]');
        const events = root.querySelector<HTMLElement>('[data-testid="nav-events"]');
        expect(board, `${label}: no Board tab`).not.toBeNull();
        expect(events, `${label}: no Events tab`).not.toBeNull();

        expect(
          nav!.scrollWidth,
          `${label}: the tab strip overflows horizontally (${nav!.scrollWidth} > ${window.innerWidth})`,
        ).toBeLessThanOrEqual(window.innerWidth);

        const rect = events!.getBoundingClientRect();
        expect(rect.right, `${label}: the Events tab's right edge is past the viewport`).toBeLessThanOrEqual(
          window.innerWidth,
        );
      }
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});
