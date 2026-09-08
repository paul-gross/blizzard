import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { page } from 'vitest/browser';

import { GlanceView, type Vitals } from './glance-view';

const VITALS: Vitals = {
  needsYou: 1,
  running: 1,
  runnersUpLabel: '7/257',
  live: true,
  liveLabel: 'live',
};

/**
 * The glance board's phone-width column, exercised in a real Chromium rather
 * than jsdom, which does not resolve its actual row positions or overflow. The
 * section sequence is load-bearing: attention comes first, then motion, the
 * queue's next dispatches, and only then completed work.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`)
 * because it needs `--browsers=ChromiumHeadless`, not jsdom — run it through
 * `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
describe('glance board layout shell sweep (web:shell-sweep)', () => {
  it('keeps its mobile sections ordered and overflow-free at phone widths', async () => {
    const pageErrors: string[] = [];
    const onError = (event: ErrorEvent) => pageErrors.push(event.message);
    const onRejection = (event: PromiseRejectionEvent) => pageErrors.push(String(event.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    await TestBed.configureTestingModule({
      imports: [GlanceView],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const fixture = TestBed.createComponent(GlanceView);
    fixture.componentRef.setInput('vitals', VITALS);
    fixture.componentRef.setInput('needsYou', [
      { chunkId: 'ch_needs', shortId: 'ch_needs', runnerId: 'runner-with-a-long-name', tone: 'needs', pillLabel: 'needs human', sub: 'Review the delivery.' },
    ]);
    fixture.componentRef.setInput('needsYouState', 'ready');
    fixture.componentRef.setInput('inMotion', [
      { chunkId: 'ch_motion', shortId: 'ch_motion', runnerId: 'runner-with-a-long-name', node: 'deliver', pillLabel: 'deliver', costUsd: 1.25, costPartial: false },
    ]);
    fixture.componentRef.setInput('inMotionState', 'ready');
    fixture.componentRef.setInput('upNext', [
      { chunkId: 'ch_next', shortId: 'ch_next', node: 'build-a-very-long-upcoming-node-name-that-must-wrap-inside-the-panel' },
    ]);
    fixture.componentRef.setInput('upNextState', 'ready');
    fixture.componentRef.setInput('doneToday', [
      { chunkId: 'ch_done', shortId: 'ch_done', pointerLabel: 'blizzard#79' },
    ]);
    fixture.componentRef.setInput('doneTodayState', 'ready');
    fixture.componentRef.setInput('doneTodayTotal', 257);
    fixture.componentRef.setInput('spendState', 'ready');
    const root = fixture.nativeElement as HTMLElement;
    // The production route gives the host a bounded, scrolling height. This
    // sweep measures section flow, so let the standalone host grow instead of
    // flex-shrinking its panels into overlapping zero-height bodies.
    root.style.cssText = 'height: auto; overflow: visible;';
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      for (const width of [390, 320]) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const panels = ['needs-you-panel', 'in-motion-panel', 'up-next-panel', 'done-today-panel'].map((testid) => {
          const panel = root.querySelector<HTMLElement>(`[data-testid="${testid}"]`);
          expect(panel, `${width}px: ${testid} is absent`).not.toBeNull();
          return panel!;
        });
        for (let index = 1; index < panels.length; index += 1) {
          expect(
            panels[index].getBoundingClientRect().top,
            `${width}px: ${panels[index].getAttribute('data-testid')} is not below the preceding section`,
          ).toBeGreaterThan(panels[index - 1].getBoundingClientRect().top);
        }
        const glance = root.querySelector<HTMLElement>('[data-testid="glance-board"]')!;
        expect(
          glance.scrollWidth,
          `${width}px: glance board overflows horizontally (${glance.scrollWidth} > ${glance.clientWidth})`,
        ).toBeLessThanOrEqual(glance.clientWidth);
      }
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});
