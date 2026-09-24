import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import type { TranscriptSegmentIndexEntry, TransitionView } from '../api/hub';
import { ChunkTranscriptsTab } from './chunk-transcripts-tab';

/**
 * The transcript segment list's harness-provenance badges (blizzard#441), the tooled
 * half of `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method — a
 * real, headless-Chromium proof that two segments recording distinct harnesses render
 * two genuinely distinct badges at the board's narrow, mobile-reachable width, rather
 * than jsdom's un-laid-out approximation.
 *
 * Excluded from the default `ng test` run (`angular.json`'s `test.exclude`) because it
 * needs `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const HISTORY: readonly TransitionView[] = [
  {
    from_node_id: 'nd_build',
    from_node_name: 'Build',
    to_node_id: 'nd_review',
    to_node_name: 'Review',
    choice_name: 'pass',
    epoch: 1,
    recorded_at: '2026-08-09T00:00:00+00:00',
  },
];

function segment(overrides: Partial<TranscriptSegmentIndexEntry> = {}): TranscriptSegmentIndexEntry {
  return {
    segment_id: 'sg_1',
    node_id: 'nd_build',
    epoch: 1,
    spawn_generation: 1,
    turn_range_start: 0,
    turn_range_end: 10,
    final: true,
    truncated: false,
    byte_count: 100,
    normalizer_version: 'v1',
    harness_version: null,
    received_at: '2026-08-09T00:00:00+00:00',
    ...overrides,
  };
}

const SEGMENTS: readonly TranscriptSegmentIndexEntry[] = [
  segment({ segment_id: 'sg_claude', spawn_generation: 1, harness_id: 'claude_code', harness_version: '1.2.3' }),
  segment({ segment_id: 'sg_codex', spawn_generation: 2, harness_id: 'codex', harness_version: '4.5.6' }),
];

describe('chunk transcripts harness-provenance layout shell sweep (web:shell-sweep, blizzard#441)', () => {
  it('keeps two segments recording distinct harnesses visually distinct with no page errors or horizontal overflow at ~390px', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    await TestBed.configureTestingModule({
      imports: [ChunkTranscriptsTab],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ChunkTranscriptsTab);
    fixture.componentRef.setInput('history', HISTORY);
    fixture.componentRef.setInput('segments', SEGMENTS);
    fixture.componentRef.setInput('indexState', 'ready');
    fixture.componentRef.setInput('segmentState', 'ready');
    fixture.componentRef.setInput('segmentId', 'sg_claude');
    fixture.componentRef.setInput('segmentData', { segment_id: 'sg_claude', final: true, truncated: false, turns: [] });
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(390, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const tab = root.querySelector<HTMLElement>('[data-testid="chunk-transcripts-tab"]')!;
      expect(tab).not.toBeNull();

      const badges = [...root.querySelectorAll<HTMLElement>('[data-testid="transcripts-tab-nav"] [data-testid="transcript-segment-harness"]')];
      expect(badges).toHaveLength(2);
      const labels = [...root.querySelectorAll('[data-testid="transcript-segment-item"]')].map((item) =>
        item.querySelector('span')?.textContent?.trim(),
      );
      expect(labels).toEqual(['Segment 1', 'Segment 2']);

      const claude = root.querySelector<HTMLElement>('[data-testid="transcripts-tab-nav"] [data-harness-id="claude_code"]')!;
      const codex = root.querySelector<HTMLElement>('[data-testid="transcripts-tab-nav"] [data-harness-id="codex"]')!;
      expect(claude).not.toBeNull();
      expect(codex).not.toBeNull();
      expect(claude.textContent?.trim()).toBe('claude code');
      expect(codex.textContent?.trim()).toBe('codex');
      const openBadge = root.querySelector<HTMLElement>('[data-testid="transcript-segment-body"] [data-harness-id="claude_code"]')!;
      expect(openBadge.textContent?.trim()).toBe('claude code');
      // Two distinct rows, not overlapping.
      expect(claude.getBoundingClientRect().top).toBeLessThan(codex.getBoundingClientRect().top);

      expect(
        tab.scrollWidth,
        `tab overflows horizontally at 390px (${tab.scrollWidth} > ${tab.clientWidth})`,
      ).toBeLessThanOrEqual(tab.clientWidth);

      fixture.componentRef.setInput('segmentId', 'sg_codex');
      fixture.componentRef.setInput('segmentState', 'ready');
      fixture.componentRef.setInput('segmentData', { segment_id: 'sg_codex', final: true, truncated: false, turns: [] });
      await fixture.whenStable();
      expect(root.querySelector('[data-testid="transcript-continued-from"]')?.textContent).toContain('segment 1');

      fixture.componentRef.setInput('segmentId', 'sg_claude');
      fixture.componentRef.setInput('segmentData', { segment_id: 'sg_claude', final: true, truncated: false, turns: [] });
      await fixture.whenStable();
      expect(root.querySelector('[data-testid="transcript-continues-in"]')?.textContent).toContain('segment 2');
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});
