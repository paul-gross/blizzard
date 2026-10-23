import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { page } from 'vitest/browser';

import { GlanceView, type Vitals } from './glance-view';

const VITALS: Vitals = {
  needsYou: 2,
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
 * See `blizzard-context:/verification/blizzard/commands/web/shell-sweep.md`
 * §web:shell-sweep.
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
      { chunkId: 'ch_needs', shortId: 'ch_needs', lines: [{ label: 'toy-api#4', title: 'Add idempotency keys to POST /payments so retried requests from flaky mobile networks never double-charge a customer' }], itemsRead: true, status: null, sub: 'Review the delivery.' },
      { chunkId: 'ch_gate', shortId: 'ch_gate', lines: [], itemsRead: true, status: 'gate', sub: 'approve-a-very-long-gate-node-name-that-must-wrap-inside-the-panel' },
    ]);
    fixture.componentRef.setInput('needsYouState', 'ready');
    fixture.componentRef.setInput('inMotion', [
      { chunkId: 'ch_motion', shortId: 'ch_motion', node: 'deliver', lines: [{ label: 'toy-api#4', title: 'Add idempotency keys to POST /payments so retried requests from flaky mobile networks never double-charge a customer' }], itemsRead: true, status: 'delivering', costUsd: 1.25, costPartial: false, estimatedCostUsd: null },
    ]);
    fixture.componentRef.setInput('inMotionState', 'ready');
    fixture.componentRef.setInput('upNext', [
      { chunkId: 'ch_next', shortId: 'ch_next', node: 'build-a-very-long-upcoming-node-name-that-must-wrap-inside-the-panel', lines: [{ label: 'toy-web#5', title: null }], itemsRead: false },
    ]);
    fixture.componentRef.setInput('upNextState', 'ready');
    fixture.componentRef.setInput('doneToday', [
      { chunkId: 'ch_done', shortId: 'ch_done', lines: [{ label: 'blizzard#79', title: 'Done item' }], itemsRead: true, status: 'stopped' },
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
        const gateRow = root.querySelector<HTMLElement>('[data-testid="needs-you-row"][data-chunk="ch_gate"]');
        expect(gateRow?.textContent, `${width}px: the gate row is absent`).toContain('gate');
        const needsYou = panels[0].getBoundingClientRect();
        expect(gateRow!.getBoundingClientRect().right, `${width}px: the gate row overflows its section`).toBeLessThanOrEqual(needsYou.right);
        // The day's spend sits under the vitals strip and above "Needs you".
        const spend = root.querySelector<HTMLElement>('[data-testid="glance-spend-panel"]')!;
        const vitals = root.querySelector<HTMLElement>('[data-testid="glance-vitals"]')!;
        expect(spend.getBoundingClientRect().top, `${width}px: spend is not below the vitals`).toBeGreaterThan(vitals.getBoundingClientRect().top);
        expect(panels[0].getBoundingClientRect().top, `${width}px: "Needs you" is not below spend`).toBeGreaterThan(spend.getBoundingClientRect().top);
        // A row's head: the work ref holds the left, the short id the right, on one line.
        const motionRow = root.querySelector<HTMLElement>('[data-testid="in-motion-row"][data-chunk="ch_motion"]')!;
        const ref = motionRow.querySelector<HTMLElement>('.head [data-testid="glance-work-ref"]')!.getBoundingClientRect();
        const cid = motionRow.querySelector<HTMLElement>('.head .cid')!.getBoundingClientRect();
        expect(Math.abs(ref.top - cid.top), `${width}px: the work ref and short id are not on one line`).toBeLessThan(4);
        expect(cid.left, `${width}px: the short id does not sit right of the work ref`).toBeGreaterThan(ref.right);
        expect(cid.right, `${width}px: the short id overflows its row`).toBeLessThanOrEqual(motionRow.getBoundingClientRect().right);
        // A long title wraps to two lines and clamps there.
        const title = motionRow.querySelector<HTMLElement>('[data-testid="glance-title"]')!;
        const lineHeight = parseFloat(getComputedStyle(title).lineHeight);
        expect(title.getBoundingClientRect().height, `${width}px: the title is not clamped to two lines`).toBeLessThanOrEqual(lineHeight * 2 + 1);
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
