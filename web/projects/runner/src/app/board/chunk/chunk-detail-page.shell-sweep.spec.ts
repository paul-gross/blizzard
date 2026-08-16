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
 * issue #318) — a real, headless-Chromium proof that its six stacked
 * sections (work item, issues, node history, asks · decisions, artifacts,
 * transcript) genuinely stack with no horizontal overflow at phone widths,
 * the way the hub's own chunk detail page is proven (`chunk-page-layout
 * .shell-sweep.spec.ts`). This page has no `@media` column split to collapse
 * — it is one flex column throughout — so unlike that sweep this one proves
 * the narrower claim: real content (a long artifact key, an unbroken work
 * item token) never pushes the page wider than the viewport.
 *
 * Excluded from the default `ng test runner` run (`angular.json`'s
 * `test.exclude`) because it needs `--browsers=ChromiumHeadless`, not jsdom —
 * run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const CHUNK_ID = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';
const LEASE_ID = 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR';

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

const LEASE = {
  lease_id: LEASE_ID,
  chunk_id: CHUNK_ID,
  graph_id: 'gr_1',
  node_id: 'nd_review',
  node_name: 'review',
  epoch: 1,
  session_id: 'sess-77',
  pid: 4821,
  environment_id: 'beta',
  workdir: '/ws/beta',
  created_at: '2026-07-16T11:00:00.000Z',
  last_heartbeat_at: '2026-07-16T11:59:26.000Z',
  state: 'running',
  closed_at: null,
  closure_reason: null,
};

function routes(method: string, path: string): unknown {
  if (method !== 'GET') return {};
  if (path === `/api/chunks/${CHUNK_ID}`) return DETAIL;
  if (path === `/api/chunks/${CHUNK_ID}/work-items`) return { items: [] };
  if (path === '/api/leases') return { items: [LEASE] };
  if (path === `/api/leases/${LEASE_ID}/transcript`) {
    return { lease_id: LEASE_ID, session_id: 'sess', available: true, reason: null, truncated: false, turns: [] };
  }
  return {};
}

const WIDTHS = [390, 320];

describe('runner chunk detail page shell sweep (web:shell-sweep, issue #318)', () => {
  it('stacks every section with no horizontal overflow at phone widths', async () => {
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
    const root = harness.fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);

    try {
      for (const width of WIDTHS) {
        await page.viewport(width, 900);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const label = `width=${width}`;
        const pageEl = root.querySelector<HTMLElement>('[data-testid="chunk-detail-page"]')!;
        expect(pageEl, `${label}: no chunk-detail-page in the DOM`).not.toBeNull();
        expect(
          pageEl.scrollWidth,
          `${label}: the page overflows horizontally (${pageEl.scrollWidth} > ${window.innerWidth})`,
        ).toBeLessThanOrEqual(window.innerWidth);

        const sections = Array.from(root.querySelectorAll<HTMLElement>('[data-testid^="section-"]'));
        expect(sections.length, `${label}: no sections rendered`).toBeGreaterThan(0);
        const tops = sections.map((s) => s.getBoundingClientRect().top);
        const distinctTops = new Set(tops);
        expect(distinctTops.size, `${label}: sections did not stack — tops were ${tops.join(', ')}`).toBe(sections.length);
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
   * The issue pane's error status (`ChunkIssuePane`'s `fleet-kit-async-state`,
   * default `placement="center"`) centers a full sentence rather than a short
   * label — unlike the loading/empty copy elsewhere on this page, long enough
   * that a real environment with no forge configured (this env's own runner
   * API returns 503 for `/work-items`) renders it at real phone widths. A
   * page-level `scrollWidth` check alone (the test above) cannot catch this:
   * the status line is `position: absolute` and was clipped by an ancestor
   * without ever pushing the page wider, so a bare word-clipped fragment
   * rendered with no page-wide horizontal scroll to show for it.
   */
  it('keeps the issue pane error status text within its section at phone widths', async () => {
    const stub = stubRequestClient(runnerClient, (method: string, path: string): unknown => {
      if (method !== 'GET') return {};
      if (path === `/api/chunks/${CHUNK_ID}`) return DETAIL;
      if (path === `/api/chunks/${CHUNK_ID}/work-items`) return stubError(503, { detail: 'no work source is configured' });
      if (path === '/api/leases') return { items: [LEASE] };
      if (path === `/api/leases/${LEASE_ID}/transcript`) {
        return { lease_id: LEASE_ID, session_id: 'sess', available: true, reason: null, truncated: false, turns: [] };
      }
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
        expect(status!.textContent?.trim()).toBe('Could not reach the forge — issue content is unavailable.');
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
