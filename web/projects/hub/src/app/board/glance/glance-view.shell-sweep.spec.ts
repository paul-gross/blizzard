import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { page } from 'vitest/browser';

import type { GlanceRow } from './glance-board.model';
import { GlanceView, type Vitals } from './glance-view';

const VITALS: Vitals = {
  needsYou: 2,
  running: 1,
  ready: 1,
  doneToday: 1,
  runnersUpLabel: '7/257',
};

const LONG_TITLE = 'Add idempotency keys to POST /payments so retried requests from flaky mobile networks never double-charge a customer';

const row = (chunkId: string, fields: Partial<GlanceRow>): GlanceRow => ({
  chunkId,
  shortId: chunkId,
  lane: 'running',
  status: 'running',
  statusWord: null,
  node: 'build',
  runner: null,
  gated: false,
  ask: null,
  lines: [],
  itemsRead: true,
  costUsd: 0,
  costPartial: false,
  estimatedCostUsd: null,
  projectColor: null,
  ...fields,
});

/**
 * The glance board's phone-width column, exercised in a real Chromium rather
 * than jsdom, which does not resolve its actual row positions or overflow. The
 * sequence is load-bearing: the vitals and the day's spend, then one list of
 * cards — attention and work in flight, READY over the divider over BACKLOG,
 * then completed work.
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
    fixture.componentRef.setInput('list', {
      live: [
        row('ch_needs', { lane: 'needs', status: 'needs_human', lines: [{ label: 'toy-api#4', title: LONG_TITLE }], ask: 'Review the delivery.' }),
        row('ch_gate', { lane: 'waiting', status: 'waiting_on_human', node: 'approve-a-very-long-gate-node-name-that-must-clip-inside-the-card', gated: true }),
        row('ch_motion', {
          node: 'deliver-a-very-long-node-name-that-must-clip-inside-the-card',
          lines: [{ label: 'toy-api#4', title: LONG_TITLE }],
          runner: { host: 'a-very-long-runner-host-name', name: 'a-very-long-runner-name' },
          costUsd: 1.25,
        }),
      ],
      ready: [row('ch_next', { lane: 'ready', status: 'ready', lines: [{ label: 'toy-web#5', title: null }], itemsRead: false })],
      backlog: [row('ch_later', { lane: 'notready', status: 'not_ready' })],
      done: [row('ch_done', { lane: 'done', status: 'stopped', statusWord: 'stopped', lines: [{ label: 'blizzard#79', title: 'Done item' }] })],
    });
    fixture.componentRef.setInput('listState', 'ready');
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

        const items = [...root.querySelectorAll<HTMLElement>('[data-testid="glance-list"] > li')];
        expect(items.map((li) => li.dataset['chunk'] ?? li.dataset['testid'])).toEqual([
          'ch_needs',
          'ch_gate',
          'ch_motion',
          'ch_next',
          'glance-ready-divider',
          'ch_later',
          'ch_done',
        ]);
        for (let index = 1; index < items.length; index += 1) {
          expect(items[index].getBoundingClientRect().top, `${width}px: list item ${index} is not below the one before it`).toBeGreaterThan(
            items[index - 1].getBoundingClientRect().top,
          );
        }
        // The day's spend sits under the vitals strip and above the list.
        const spend = root.querySelector<HTMLElement>('[data-testid="glance-spend-panel"]')!;
        const vitals = root.querySelector<HTMLElement>('[data-testid="glance-vitals"]')!;
        expect(spend.getBoundingClientRect().top, `${width}px: spend is not below the vitals`).toBeGreaterThan(vitals.getBoundingClientRect().top);
        expect(items[0].getBoundingClientRect().top, `${width}px: the list is not below spend`).toBeGreaterThan(spend.getBoundingClientRect().top);
        // A card's head: the work ref holds the left, the short id the right, on one line.
        const motionRow = root.querySelector<HTMLElement>('[data-testid="glance-card"][data-chunk="ch_motion"]')!;
        const card = motionRow.getBoundingClientRect();
        const ref = motionRow.querySelector<HTMLElement>('.head [data-testid="glance-work-ref"]')!.getBoundingClientRect();
        const cid = motionRow.querySelector<HTMLElement>('.head .cid')!.getBoundingClientRect();
        expect(Math.abs(ref.top - cid.top), `${width}px: the work ref and short id are not on one line`).toBeLessThan(4);
        expect(cid.left, `${width}px: the short id does not sit right of the work ref`).toBeGreaterThan(ref.right);
        expect(cid.right, `${width}px: the short id overflows its card`).toBeLessThanOrEqual(card.right);
        // The meta line holds one line: a long node and runner clip, and the cost keeps its place inside the card.
        const step = motionRow.querySelector<HTMLElement>('.step')!.getBoundingClientRect();
        const runner = motionRow.querySelector<HTMLElement>('[data-testid="glance-runner"]')!.getBoundingClientRect();
        const cost = motionRow.querySelector<HTMLElement>('[data-testid="glance-cost"]')!.getBoundingClientRect();
        expect(Math.abs(step.top - runner.top), `${width}px: the node and runner are not on one line`).toBeLessThan(4);
        expect(cost.right, `${width}px: the cost overflows its card`).toBeLessThanOrEqual(card.right);
        expect(runner.right, `${width}px: the runner runs into the cost`).toBeLessThanOrEqual(cost.left);
        const gateCard = root.querySelector<HTMLElement>('[data-testid="glance-card"][data-chunk="ch_gate"]')!;
        expect(gateCard.querySelector('.gate')!.getBoundingClientRect().right, `${width}px: the gate pill overflows its card`).toBeLessThanOrEqual(
          gateCard.getBoundingClientRect().right,
        );
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
