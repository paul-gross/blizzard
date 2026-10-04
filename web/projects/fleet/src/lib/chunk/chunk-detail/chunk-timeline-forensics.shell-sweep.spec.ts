import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { page } from 'vitest/browser';

import type { ChunkDetail } from '../../api/hub';
import { ChunkTimeline } from './chunk-timeline';
import { ChunkTimelineSelection } from './chunk-timeline-selection';

/**
 * The node-history timeline's bounce and restart rows, the tooled half of
 * `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method — a real,
 * headless-Chromium proof that both rows render, in time order, without horizontal
 * overflow on the timeline and on the Node history tab's Selection list at the board's
 * mobile-reachable widths.
 *
 * Excluded from the default `ng test` run (`angular.json`'s `test.exclude`) because it
 * needs `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const DETAIL: ChunkDetail = {
  chunk_id: 'ch_01forensicsweep00000000000000',
  graph_id: 'gr_2',
  graph_name: 'second',
  status: 'running',
  current_node_id: 'nd_plan',
  current_node_name: 'plan',
  latest_epoch: 3,
  work_refs: [],
  artifacts: [],
  history: [
    { from_node_id: 'nd_build', from_node_name: 'build', to_node_id: 'nd_review', to_node_name: 'review', choice_name: 'pass', epoch: 1, recorded_at: '2026-08-09T00:00:01Z' },
  ],
  bounces: [
    {
      cause: 'result-envelope-failed-schema-validation',
      envelope: '{"verdict":"maybe","notes":"' + 'x'.repeat(400) + '"}',
      recorded_at: '2026-08-09T00:00:02Z',
    },
  ],
  restarts: [
    {
      epoch: 2,
      graph_id: 'gr_2',
      graph_name: 'second',
      from_graph_id: 'gr_1',
      from_graph_name: 'first',
      from_node_id: 'nd_review',
      from_node_name: 'review',
      to_node_id: 'nd_plan',
      to_node_name: 'plan',
      restarted_by: 'a-rather-long-operator-name@example.test',
      recorded_at: '2026-08-09T00:00:03Z',
    },
  ],
};

describe('chunk timeline bounce and restart rows shell sweep (web:shell-sweep)', () => {
  for (const width of [390, 320]) {
    it(`renders a bounce and a restart in time order with no page errors or horizontal overflow at ${width}px`, async () => {
      const pageErrors: string[] = [];
      const onError = (e: ErrorEvent) => pageErrors.push(e.message);
      const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
      window.addEventListener('error', onError);
      window.addEventListener('unhandledrejection', onRejection);

      await TestBed.configureTestingModule({
        imports: [ChunkTimeline, ChunkTimelineSelection],
        providers: [provideZonelessChangeDetection(), provideRouter([])],
      }).compileComponents();

      // One host at a time — mounting a second fixture detaches the first one's root.
      const hosts = [
        [ChunkTimeline, 'history'],
        [ChunkTimelineSelection, 'selection'],
      ] as const;
      try {
        await page.viewport(width, 800);
        for (const [component, prefix] of hosts) {
          const fixture = TestBed.createComponent<ChunkTimeline | ChunkTimelineSelection>(component);
          fixture.componentRef.setInput('detail', DETAIL);
          await fixture.whenStable();
          const root = fixture.nativeElement as HTMLElement;
          document.body.appendChild(root);
          await fixture.whenStable();
          await new Promise((resolve) => requestAnimationFrame(resolve));

          try {
            const bounce = root.querySelector<HTMLElement>(`[data-testid="${prefix}-bounce-step"]`)!;
            const restart = root.querySelector<HTMLElement>(`[data-testid="${prefix}-restart-step"]`)!;
            expect(bounce, `${prefix}: no bounce row`).not.toBeNull();
            expect(restart, `${prefix}: no restart row`).not.toBeNull();
            expect(bounce.getBoundingClientRect().height, `${prefix}: bounce row collapsed`).toBeGreaterThan(0);
            expect(bounce.getBoundingClientRect().top, `${prefix}: bounce not above restart`).toBeLessThan(
              restart.getBoundingClientRect().top,
            );
            expect(restart.querySelector(`[data-testid="${prefix}-actor"]`)?.textContent).toContain('a-rather-long-operator-name');

            const list = root.querySelector<HTMLElement>('.timeline') ?? root;
            expect(
              list.scrollWidth,
              `${prefix} overflows horizontally at ${width}px (${list.scrollWidth} > ${list.clientWidth})`,
            ).toBeLessThanOrEqual(list.clientWidth);
            expect(document.documentElement.scrollWidth, `${prefix}: page overflows at ${width}px`).toBeLessThanOrEqual(width);
          } finally {
            root.remove();
          }
        }
      } finally {
        window.removeEventListener('error', onError);
        window.removeEventListener('unhandledrejection', onRejection);
      }

      expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
    });
  }
});
