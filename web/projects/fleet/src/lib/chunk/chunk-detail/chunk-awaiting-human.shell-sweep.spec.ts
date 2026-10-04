import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import type { ChunkDetail } from '../../api/hub';
import { ChunkAwaitingHuman } from './chunk-awaiting-human';

/**
 * The Gate panel's origin line — the tooled half of `web:shell-sweep`: a real
 * headless-Chromium proof that a long runner id in "gated by runner … (runner config)"
 * wraps inside the panel at ~390px rather than forcing horizontal scroll
 * (`bzh:narrow-viewport-tier-rule`). Excluded from the default `ng test fleet` run; run it
 * via `npm run shell-sweep`.
 */
function detail(imposedBy: string | null, resolution: Partial<NonNullable<ChunkDetail['decision']>> = {}): ChunkDetail {
  return {
    chunk_id: 'ch_01gate0000000000000000000000',
    graph_id: 'gr_1',
    status: 'waiting_on_human',
    current_node_id: 'nd_gate',
    latest_epoch: 1,
    work_refs: [],
    history: [],
    artifacts: [],
    decision: {
      decision_id: 'de_01',
      chunk_id: 'ch_01gate0000000000000000000000',
      node_id: 'nd_gate',
      node_name: 'approve-gate',
      epoch: 1,
      submitted_at: '2026-07-13T00:00:01Z',
      choices: [
        { name: 'approve', description: 'Ship it.' },
        { name: 'reject', description: 'Send it back.' },
      ],
      transitioned: false,
      imposed_by_runner_id: imposedBy,
      ...resolution,
    },
  };
}

async function mount(imposedBy: string | null, resolution: Partial<NonNullable<ChunkDetail['decision']>> = {}) {
  await TestBed.configureTestingModule({
    imports: [ChunkAwaitingHuman],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkAwaitingHuman);
  fixture.componentRef.setInput('detail', detail(imposedBy, resolution));
  fixture.componentRef.setInput('canResolve', true);
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await fixture.whenStable();
  return root;
}

describe('gate panel origin line shell sweep (web:shell-sweep)', () => {
  it.each([
    ['a runner-imposed gate', 'r-a-runner-id-long-enough-to-need-wrapping-on-a-narrow-phone-0123456789', 'gated by runner'],
    ['a graph-declared gate', null, 'declared by the graph'],
  ])('keeps %s inside the panel with no page errors or horizontal overflow at ~390px', async (_name, imposedBy, text) => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    const root = await mount(imposedBy);

    try {
      await page.viewport(390, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const origin = root.querySelector<HTMLElement>('[data-testid="decision-origin"]')!;
      expect(origin.textContent).toContain(text);
      expect(origin.getBoundingClientRect().right).toBeLessThanOrEqual(document.documentElement.clientWidth);
      expect(
        root.scrollWidth,
        `gate panel overflows horizontally at 390px (${root.scrollWidth} > ${root.clientWidth})`,
      ).toBeLessThanOrEqual(root.clientWidth);
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});

describe('gate panel resolved line shell sweep (web:shell-sweep)', () => {
  it('keeps a resolved gate’s "<choice> by <who>, <when>" line inside the panel at 390px and 320px', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    const root = await mount(null, {
      resolved_choice: 'approve-with-a-choice-name-long-enough-to-need-wrapping',
      resolved_by: 'an-operator-username-long-enough-to-need-wrapping@example.com',
      resolved_at: '2026-07-13T00:02:00Z',
    });

    try {
      for (const width of [390, 320]) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const line = root.querySelector<HTMLElement>('[data-testid="decision-resolved-line"]');
        expect(line?.textContent, `${width}px: the resolved line is absent`).toContain(' by ');
        expect(root.querySelector('[data-testid="decision-choice"]'), `${width}px: choices still offered`).toBeNull();
        expect(line!.getBoundingClientRect().right).toBeLessThanOrEqual(document.documentElement.clientWidth);
        expect(
          root.scrollWidth,
          `gate panel overflows horizontally at ${width}px (${root.scrollWidth} > ${root.clientWidth})`,
        ).toBeLessThanOrEqual(root.clientWidth);
      }
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});
