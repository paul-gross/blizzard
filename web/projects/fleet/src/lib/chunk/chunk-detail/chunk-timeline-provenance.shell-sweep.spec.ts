import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { page } from 'vitest/browser';

import type { ChunkDetail } from '../../api/hub';
import { chunkDetail, chunkUsageView, transitionView } from 'fleet/testing';
import { ChunkTimeline } from './chunk-timeline';

/**
 * The node-history timeline's harness-provenance badges, the tooled half
 * of `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method — a real,
 * headless-Chromium proof that two steps' own recorded harnesses render two genuinely
 * distinct badges beside their usage figures at the board's narrow, mobile-reachable
 * width, rather than jsdom's un-laid-out approximation.
 *
 * Excluded from the default `ng test` run (`angular.json`'s `test.exclude`) because it
 * needs `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const DETAIL: ChunkDetail = chunkDetail({
  chunk_id: 'ch_01provenance00000000000000000',
  graph_id: 'gr_1',
  graph_name: 'default',
  status: 'done',
  current_node_id: 'nd_done',
  current_node_name: null,
  latest_epoch: 2,
  work_refs: [],
  history: [
    transitionView({ from_node_id: 'nd_build', to_node_id: 'nd_review', choice_name: 'pass', epoch: 1, recorded_at: '2026-08-09T00:00:01Z', from_node_name: null, to_node_name: null, graph_id: 'gr_1', graph_name: null }),
    transitionView({ from_node_id: 'nd_review', to_node_id: 'nd_done', choice_name: 'landed', epoch: 2, recorded_at: '2026-08-09T00:00:02Z', from_node_name: null, to_node_name: null, graph_id: 'gr_1', graph_name: null }),
  ],
  artifacts: [],
  usage: [
    chunkUsageView({
      node_id: 'nd_build',
      epoch: 1,
      kind: 'spawn',
      model: 'claude-opus-4-8',
      harness_id: 'claude_code',
      harness_version: '1.2.3',
      input_tokens: 1200,
      output_tokens: 800,
      cache_read_tokens: 300,
      cache_create_tokens: 100,
      cost_usd: 0.42,
      cost_partial: false,
      estimated_cost_usd: null,
    }),
    chunkUsageView({
      node_id: 'nd_review',
      epoch: 2,
      kind: 'spawn',
      model: 'gpt-5.6',
      harness_id: 'codex',
      harness_version: '4.5.6',
      input_tokens: 900,
      output_tokens: 400,
      cache_read_tokens: 100,
      cache_create_tokens: 50,
      cost_usd: 0.11,
      cost_partial: false,
      estimated_cost_usd: null,
    }),
  ],
});

// The same two-step history, but the second step is a subscription invocation: no
// billed cost, only a runner-reported estimate (an estimate-only row is kept
// non-partial), its usage folded by `chunk-timeline-rows.ts`.
const ESTIMATE_DETAIL: ChunkDetail = {
  ...DETAIL,
  chunk_id: 'ch_01estimateprov0000000000000',
  usage: [
    DETAIL.usage![0],
    chunkUsageView({ ...DETAIL.usage![1], cost_usd: null, estimated_cost_usd: 0.07, cost_partial: false, harness_id: null, harness_version: null }),
  ],
};

describe('chunk timeline harness-provenance layout shell sweep (web:shell-sweep, blizzard#441)', () => {
  it('keeps two steps recording distinct harnesses visually distinct with no page errors or horizontal overflow at ~390px', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    await TestBed.configureTestingModule({
      imports: [ChunkTimeline],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', DETAIL);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(390, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const badges = [...root.querySelectorAll<HTMLElement>('[data-testid="history-step-harness"]')];
      expect(badges).toHaveLength(2);

      const claude = root.querySelector<HTMLElement>('[data-harness-id="claude_code"]')!;
      const codex = root.querySelector<HTMLElement>('[data-harness-id="codex"]')!;
      expect(claude).not.toBeNull();
      expect(codex).not.toBeNull();
      expect(claude.textContent?.trim()).toBe('claude code');
      expect(codex.textContent?.trim()).toBe('codex');
      // Two distinct rows, not overlapping.
      expect(claude.getBoundingClientRect().top).toBeLessThan(codex.getBoundingClientRect().top);

      const steps = root.querySelector<HTMLElement>('.timeline') ?? root;
      expect(
        steps.scrollWidth,
        `timeline overflows horizontally at 390px (${steps.scrollWidth} > ${steps.clientWidth})`,
      ).toBeLessThanOrEqual(steps.clientWidth);
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });

  it('renders a step’s own estimate folded into its one cost figure on the timeline, with no overflow at ~390px', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    await TestBed.configureTestingModule({
      imports: [ChunkTimeline],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();

    const timelineFixture = TestBed.createComponent(ChunkTimeline);
    timelineFixture.componentRef.setInput('detail', ESTIMATE_DETAIL);
    await timelineFixture.whenStable();
    const timelineRoot = timelineFixture.nativeElement as HTMLElement;
    document.body.appendChild(timelineRoot);
    await timelineFixture.whenStable();

    try {
      await page.viewport(390, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const timelineCosts = timelineRoot.querySelectorAll<HTMLElement>('[data-testid="history-step-efficiency"] .part:first-child');
      expect(timelineCosts[1]?.textContent?.trim()).toBe('~$0.07');


      const timelineList = timelineRoot.querySelector<HTMLElement>('.timeline') ?? timelineRoot;
      expect(
        timelineList.scrollWidth,
        `timeline overflows horizontally at 390px (${timelineList.scrollWidth} > ${timelineList.clientWidth})`,
      ).toBeLessThanOrEqual(timelineList.clientWidth);

    } finally {
      timelineRoot.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });

  for (const width of [390, 320]) {
    it(`gives each step's usage its own row under an unclipped verdict, spanning every column but the stamp, at ${width}px`, async () => {
      await TestBed.configureTestingModule({
        imports: [ChunkTimeline],
        providers: [provideZonelessChangeDetection(), provideRouter([])],
      }).compileComponents();
      const fixture = TestBed.createComponent(ChunkTimeline);
      fixture.componentRef.setInput('detail', DETAIL);
      await fixture.whenStable();
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);
      await fixture.whenStable();

      try {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const steps = [...root.querySelectorAll<HTMLElement>('[data-testid="history-step"]')];
        expect(steps.length, 'fixture defect — no history steps rendered').toBeGreaterThan(0);
        for (const step of steps) {
          const usage = step.querySelector<HTMLElement>('[data-testid="history-step-usage"]')!;
          const verdict = step.querySelector<HTMLElement>('[data-testid="history-choice"]')!;
          const jg = step.querySelector<HTMLElement>('.jg')!;
          const usageRect = usage.getBoundingClientRect();
          const verdictRect = verdict.getBoundingClientRect();
          const stampRect = step.querySelector<HTMLElement>('[data-testid="history-when"]')!.getBoundingClientRect();
          const markRect = step.querySelector<HTMLElement>('.att')!.getBoundingClientRect();

          expect(usageRect.top, 'usage shares the verdict line').toBeGreaterThanOrEqual(verdictRect.bottom - 0.5);
          expect(usageRect.left, 'usage does not start at the row’s first column').toBeLessThanOrEqual(markRect.left + 0.5);
          expect(usageRect.right, 'usage runs into the stamp column').toBeLessThanOrEqual(stampRect.left + 0.5);
          expect(stampRect.top, 'the stamp does not span the entry from its main row').toBeLessThanOrEqual(verdictRect.top + 0.5);
          expect(verdictRect.right, 'verdict clipped past its cell').toBeLessThanOrEqual(jg.getBoundingClientRect().right + 0.5);
        }
      } finally {
        root.remove();
      }
    });
  }
});
