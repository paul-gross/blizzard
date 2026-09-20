import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { page } from 'vitest/browser';

import type { ChunkDetail } from '../api/hub';
import { ChunkTimeline } from './chunk-timeline';

/**
 * The node-history timeline's harness-provenance badges (blizzard#441), the tooled half
 * of `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method — a real,
 * headless-Chromium proof that two steps' own recorded harnesses render two genuinely
 * distinct badges beside their usage figures at the board's narrow, mobile-reachable
 * width, rather than jsdom's un-laid-out approximation.
 *
 * Excluded from the default `ng test` run (`angular.json`'s `test.exclude`) because it
 * needs `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const DETAIL: ChunkDetail = {
  chunk_id: 'ch_01provenance00000000000000000',
  graph_id: 'gr_1',
  graph_name: 'default',
  status: 'done',
  current_node_id: 'nd_done',
  current_node_name: null,
  latest_epoch: 2,
  work_refs: [],
  history: [
    { from_node_id: 'nd_build', to_node_id: 'nd_review', choice_name: 'pass', epoch: 1, recorded_at: '2026-08-09T00:00:01Z' },
    { from_node_id: 'nd_review', to_node_id: 'nd_done', choice_name: 'landed', epoch: 2, recorded_at: '2026-08-09T00:00:02Z' },
  ],
  artifacts: [],
  usage: [
    {
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
    },
    {
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
    },
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
});
