import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { commands, page } from 'vitest/browser';

import type { ChunkDetail } from '../../api/hub';
import { bounceView, chunkDetail, transitionView } from 'fleet/testing';
import { ChunkTimeline } from './chunk-timeline';

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
const DETAIL: ChunkDetail = chunkDetail({
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
    transitionView({ from_node_id: 'nd_build', from_node_name: 'build', to_node_id: 'nd_review', to_node_name: 'review', choice_name: 'pass', epoch: 1, recorded_at: '2026-08-09T00:00:01Z', graph_id: 'gr_2', graph_name: null }),
  ],
  bounces: [
    {
      cause: 'result-envelope-failed-schema-validation',
      envelope: '{"verdict":"maybe","notes":"' + 'x'.repeat(400) + '"}',
      recorded_at: '2026-08-09T00:00:02Z',
      withdrawn: false,
      rework: false,
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
      note: null,
      decision_id: null,
    },
  ],
});

describe('chunk timeline bounce and restart rows shell sweep (web:shell-sweep)', () => {
  for (const width of [390, 320]) {
    it(`bounds long identityless and identified diagnostics to five lines without losing text at ${width}px`, async () => {
      await page.viewport(width, 800);
      const tokens = document.createElement('style');
      tokens.textContent = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
      document.head.appendChild(tokens);
      await TestBed.configureTestingModule({
        imports: [ChunkTimeline],
        providers: [provideZonelessChangeDetection(), provideRouter([])],
      }).compileComponents();
      const plain = 'Permission denied while opening the repository. '.repeat(80);
      const unknown = JSON.stringify({ error: 'unrecognized-diagnostic-'.repeat(200), code: 403 });
      const identified = JSON.stringify({
        cause: 'failure', detail: plain,
        unlanded: [{ repo: 'org/repo', branch: 'work', commit: 'abc', pr: { number: 42, url: 'https://example.test/pull/42' } }],
      });
      const fixture = TestBed.createComponent(ChunkTimeline);
      fixture.componentRef.setInput('detail', chunkDetail({
        status: 'done', history: [],
        bounces: [plain, unknown, identified].map((envelope, index) => bounceView({ envelope, recorded_at: `2026-08-09T00:00:0${index}Z` })),
      }));
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);
      try {
        await fixture.whenStable();
        await new Promise((resolve) => requestAnimationFrame(resolve));
        const rows = [...root.querySelectorAll<HTMLElement>('[data-testid="history-bounce-step"]')];
        expect(rows).toHaveLength(3);
        for (const [index, row] of rows.entries()) {
          const diagnostic = row.querySelector<HTMLElement>(index === 2 ? '.bounce-reason' : '.bounce-diagnostic')!;
          const full = index === 1 ? unknown : plain;
          expect(diagnostic.textContent).toBe(full);
          expect(diagnostic.title).toBe(full);
          expect(row.title).toBe(full);
          const style = getComputedStyle(diagnostic);
          expect(style.webkitLineClamp).toBe('5');
          const lineHeight = Number.parseFloat(style.lineHeight);
          expect(lineHeight).toBeGreaterThan(0);
          expect(diagnostic.getBoundingClientRect().height).toBeGreaterThan(0);
          expect(diagnostic.getBoundingClientRect().height).toBeLessThanOrEqual(5 * lineHeight + 1);
          expect(diagnostic.scrollHeight).toBeGreaterThan(diagnostic.clientHeight);
          expect(diagnostic.getBoundingClientRect().right).toBeLessThanOrEqual(root.getBoundingClientRect().right + 1);
          expect(row.scrollWidth).toBeLessThanOrEqual(row.clientWidth);
        }
        expect(rows[2].querySelector('.verdict')?.textContent).toBe('org/repo · PR #42');
        expect(root.scrollWidth).toBeLessThanOrEqual(root.clientWidth);
        expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
      } finally {
        fixture.destroy();
        root.remove();
        tokens.remove();
      }
    });

    it(`renders a bounce and a restart in time order with no page errors or horizontal overflow at ${width}px`, async () => {
      const pageErrors: string[] = [];
      const onError = (e: ErrorEvent) => pageErrors.push(e.message);
      const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
      window.addEventListener('error', onError);
      window.addEventListener('unhandledrejection', onRejection);

      await TestBed.configureTestingModule({
        imports: [ChunkTimeline],
        providers: [provideZonelessChangeDetection(), provideRouter([])],
      }).compileComponents();

      const hosts = [[ChunkTimeline, 'history']] as const;
      try {
        await page.viewport(width, 800);
        for (const [component, prefix] of hosts) {
          const fixture = TestBed.createComponent(component);
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
