import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { settle, stubError, stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { ChunkDetailPage } from './chunk-detail-page';

/**
 * The runner-local chunk detail page's half of `web:shell-sweep`
 * (`blizzard-context:/verification/blizzard.md` bzh:web-shell-sweep,
 * now tabbed, further widened for Node history) — a real,
 * headless-Chromium proof that every tab — General (work item, issues, node
 * history summary, asks · decisions), Node history, Artifacts, Transcripts —
 * renders with no horizontal overflow at phone widths, the way the hub's own
 * chunk detail page is proven (`chunk-page-layout.shell-sweep.spec.ts`). The
 * General tab's `@media (min-width: 720px)` two-column grid
 * (`chunk-general-tab.ts`) collapses below that breakpoint the same way the
 * hub's own copy does; jsdom parses that query without ever evaluating it, so
 * `web:unit-test` cannot see the collapse — real content (a long artifact
 * key, an unbroken work item token) never pushes the page wider than the
 * viewport.
 *
 * The "stacks its own sections" proof only applies to General, whose own sections carry
 * a `section-`-prefixed testid (`fleet-kit-panel`'s own convention); Artifacts, Node
 * history, and Transcripts (runner-node-grouped-transcripts Phase 4 — now the shared
 * `fleet-chunk-transcripts-container` nav-plus-viewer pane, not the prior lease-chip
 * `section-transcript` panel) are each one nav-plus-viewer pane rather than a stack of
 * independent panels (`fleet-chunk-artifacts-panel`, `app-chunk-node-history-tab`,
 * `fleet-chunk-transcripts-container`) — for these three, the horizontal-overflow check
 * above already stands in for it: the Artifacts tab defaults its viewer to the fixture's
 * single (deliberately long-keyed) artifact with no click needed, so the overflow check
 * already covers this file's own defect class for that tab.
 *
 * Excluded from the default `ng test runner` run (`angular.json`'s
 * `test.exclude`) because it needs `--browsers=ChromiumHeadless`, not jsdom —
 * run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const CHUNK_ID = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';

const DETAIL = {
  chunk_id: CHUNK_ID,
  graph_id: 'gr_1',
  graph_name: 'default',
  current_node_id: 'nd_review',
  current_node_name: 'review',
  latest_epoch: 1,
  status: 'running',
  // A bare, unbroken token far wider than a 320px phone — the same defect
  // class `chunk-page-layout.shell-sweep.spec.ts`'s own long-repo-path case
  // proves against, here for the artifact key column instead.
  work_refs: [{ source: 'blizzard', ref: '318', label: 'blizzard#318', web_url: null }],
  history: [
    {
      choice_name: 'pass',
      epoch: 1,
      from_node_id: null,
      to_node_id: 'nd_review',
      to_node_name: 'review',
      recorded_at: '2026-07-16T11:00:00.000Z',
    },
  ],
  artifacts: [
    {
      key: 'a-very-long-unbroken-artifact-key-that-would-overflow-a-narrow-phone-viewport-if-nothing-wrapped-it.retrospective.1',
      kind: 'asset',
      name: 'retrospective',
      node_id: 'nd_review',
      node_name: 'review',
      epoch: 1,
      recorded_at: '2026-07-16T11:00:00.000Z',
    },
  ],
};

function routes(method: string, path: string): unknown {
  if (method !== 'GET') return {};
  if (path === `/api/chunks/${CHUNK_ID}`) return DETAIL;
  if (path === `/api/chunks/${CHUNK_ID}/work-items`) return { items: [] };
  if (path === `/api/chunks/${CHUNK_ID}/transcripts`) return { chunk_id: CHUNK_ID, segments: [] };
  return {};
}

const WIDTHS = [390, 320];

const TABS = [
  { testid: 'tab-general', label: 'General', expectSections: true },
  { testid: 'tab-node-history', label: 'Node history', expectSections: false },
  { testid: 'tab-artifacts', label: 'Artifacts', expectSections: false },
  { testid: 'tab-transcripts', label: 'Transcripts', expectSections: false },
] as const;

describe('runner chunk detail page shell sweep (web:shell-sweep, issue #318)', () => {
  it('stacks every tab’s own sections with no horizontal overflow at phone widths', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    const stub = stubRequestClient(runnerClient, routes);
    await TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'board/chunk/:chunkId', component: ChunkDetailPage }]),
      ],
    }).compileComponents();
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}`);
    await settle(harness.fixture);
    let root = harness.fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);

    try {
      for (const width of WIDTHS) {
        await page.viewport(width, 900);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        for (const { testid: tabTestid, label: tabLabel, expectSections } of TABS) {
          root.querySelector<HTMLButtonElement>(`[data-testid="${tabTestid}"]`)?.click();
          await settle(harness.fixture);
          root = harness.fixture.nativeElement as HTMLElement;
          await new Promise((resolve) => requestAnimationFrame(resolve));

          const label = `width=${width}, tab=${tabLabel}`;
          const pageEl = root.querySelector<HTMLElement>('[data-testid="chunk-detail-page"]')!;
          expect(pageEl, `${label}: no chunk-detail-page in the DOM`).not.toBeNull();
          expect(
            pageEl.scrollWidth,
            `${label}: the page overflows horizontally (${pageEl.scrollWidth} > ${window.innerWidth})`,
          ).toBeLessThanOrEqual(window.innerWidth);

          if (expectSections) {
            const sections = Array.from(root.querySelectorAll<HTMLElement>('[data-testid^="section-"]'));
            expect(sections.length, `${label}: no sections rendered`).toBeGreaterThan(0);
            const tops = sections.map((s) => s.getBoundingClientRect().top);
            const distinctTops = new Set(tops);
            expect(distinctTops.size, `${label}: sections did not stack — tops were ${tops.join(', ')}`).toBe(
              sections.length,
            );
          }
        }
      }
    } finally {
      root.remove();
      stub.restore();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });

  /**
   * The page's own loading/error layout, which the sweep above never reaches
   * (it stubs a 200 for the detail read, so the page is always populated). The
   * page-level status line is `fleet-kit-async-state`'s absolutely-centered
   * `placement="center"`, so it resolves against its nearest positioned
   * ancestor: centering it against a box whose only in-flow content is the
   * 44px back bar paints "FAILED TO LOAD CHUNK" straight across "‹ Board".
   * This pins the fix — the status centers in `.body`, the back row's sibling,
   * which fills the space below it.
   */
  it('keeps the page-level error status clear of the back bar', async () => {
    const stub = stubRequestClient(runnerClient, (method: string, path: string): unknown => {
      if (method === 'GET' && path === `/api/chunks/${CHUNK_ID}`) return stubError(404, { detail: 'unknown chunk' });
      return routes(method, path);
    });
    await TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'board/chunk/:chunkId', component: ChunkDetailPage }]),
      ],
    }).compileComponents();
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}`);
    await settle(harness.fixture);
    const root = harness.fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);

    try {
      for (const width of WIDTHS) {
        await page.viewport(width, 900);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const label = `width=${width}`;
        const status = root.querySelector<HTMLElement>('[data-testid="chunk-detail-page-error"]');
        expect(status, `${label}: the page error status is not in the DOM`).not.toBeNull();
        const backRect = root.querySelector<HTMLElement>('[data-testid="chunk-detail-back"]')!.getBoundingClientRect();
        const statusRect = status!.getBoundingClientRect();
        expect(
          statusRect.top,
          `${label}: the error status overlaps the back bar (status top ${statusRect.top} < back bottom ${backRect.bottom})`,
        ).toBeGreaterThanOrEqual(backRect.bottom);
      }
    } finally {
      root.remove();
      stub.restore();
    }
  });

  /**
   * Regression coverage for a live-click-through defect (issue #318's verify
   * node-step): `ChunkIssuePane`'s error status used `fleet-kit-async-state`'s
   * default `placement="center"` — a full sentence, not a short label, so it
   * overflowed and got clipped mid-word at phone widths, invisible to a
   * page-level `scrollWidth` check (the test above) since the status line was
   * `position: absolute` and clipped by an ancestor without ever pushing the
   * page wider. Fixed by this page's own mount opting `ChunkIssuePane` into
   * `placement="inline"` (`chunk-detail-page.ts`) — normal flow, not
   * absolutely positioned, and scoped to this narrow-layout consumer rather
   * than every mount (the desktop dock and hub keep `'center'`, their prior
   * rendering, unaffected). This pins the fix: the full text renders, in-bounds, at phone widths, for a
   * real environment with no forge configured (this env's own runner API
   * returns 503 for `/work-items`).
   */
  it('keeps the issue pane error status text within its section at phone widths', async () => {
    const stub = stubRequestClient(runnerClient, (method: string, path: string): unknown => {
      if (method !== 'GET') return {};
      if (path === `/api/chunks/${CHUNK_ID}`) return DETAIL;
      if (path === `/api/chunks/${CHUNK_ID}/work-items`) return stubError(503, { detail: 'no work source is configured' });
      return {};
    });
    await TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'board/chunk/:chunkId', component: ChunkDetailPage }]),
      ],
    }).compileComponents();
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}`);
    await settle(harness.fixture);
    const root = harness.fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);

    try {
      for (const width of WIDTHS) {
        await page.viewport(width, 900);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const label = `width=${width}`;
        const status = root.querySelector<HTMLElement>('[data-testid="issue-error"]');
        expect(status, `${label}: issue-error status not in the DOM`).not.toBeNull();
        expect(status!.textContent?.trim()).toBe('Could not read the work items — content is unavailable.');
        const section = status!.closest<HTMLElement>('[data-testid^="section-"]')!;
        const statusRect = status!.getBoundingClientRect();
        const sectionRect = section.getBoundingClientRect();
        expect(
          statusRect.left,
          `${label}: the error status starts left of its section (${statusRect.left} < ${sectionRect.left})`,
        ).toBeGreaterThanOrEqual(sectionRect.left - 1);
        expect(
          statusRect.right,
          `${label}: the error status extends past its section (${statusRect.right} > ${sectionRect.right})`,
        ).toBeLessThanOrEqual(sectionRect.right + 1);
      }
    } finally {
      root.remove();
      stub.restore();
    }
  });
});
