import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import { FleetScopePanel, type ScopePanelVm } from './scope-panel';

/**
 * The gardening scope panel's layout (blizzard#489, the tooled half of
 * `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method) — a real,
 * headless-Chromium proof that the header, description editor, lifecycle actions, and
 * related-routines list genuinely stack at phone widths with no horizontal overflow,
 * and that a long routine name wraps inside the list rather than widening the panel —
 * a real CSS layout claim jsdom cannot make, `routine-panel.shell-sweep.spec.ts`'s own
 * reason.
 *
 * Excluded from the default `ng test fleet` run (`angular.json`'s `test.exclude`)
 * because it needs `--browsers=ChromiumHeadless`, not jsdom — run it via
 * `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const VM: ScopePanelVm = {
  slug: 'blizzard',
  description: 'the blizzard monorepo',
  retired: false,
  relatedRoutines: [
    { name: 'nightly', isDefault: true },
    { name: 'a-very-long-routine-name-that-should-wrap-rather-than-overflow-its-list', isDefault: false },
  ],
};

async function render() {
  await TestBed.configureTestingModule({
    imports: [FleetScopePanel],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(FleetScopePanel);
  fixture.componentRef.setInput('state', 'ready');
  fixture.componentRef.setInput('vm', VM);
  fixture.componentRef.setInput('canEdit', true);
  await fixture.whenStable();
  return fixture;
}

describe('gardening scope panel layout shell sweep (web:shell-sweep, blizzard#489)', () => {
  it.each([1280, 390, 320])('stacks every block with no horizontal overflow at %ipx', async (width) => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    const fixture = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(width, 900);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const panel = root.querySelector<HTMLElement>('[data-testid="gardening-scope-panel"]')!;
      expect(panel).not.toBeNull();

      const blockIds = ['gardening-scope-panel-description-input', 'gardening-scope-panel-retire'];
      for (const id of blockIds) expect(root.querySelector(`[data-testid="${id}"]`)).not.toBeNull();

      expect(
        panel.scrollWidth,
        `panel overflows horizontally at ${width}px (${panel.scrollWidth} > ${panel.clientWidth})`,
      ).toBeLessThanOrEqual(panel.clientWidth);

      const routines = root.querySelector<HTMLElement>('[data-testid="gardening-scope-panel-routines"]')!;
      expect(
        routines.scrollWidth,
        `related-routines list overflows its own width at ${width}px (${routines.scrollWidth} > ${routines.clientWidth})`,
      ).toBeLessThanOrEqual(routines.clientWidth);
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});
