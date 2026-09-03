import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet, type Routes } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, settle, stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { GardeningProposalDetail } from './gardening-proposal-detail';
import { GardeningProposalsPage } from './gardening-proposals-page';

/**
 * The garden proposal docket container's own `.gp-layout` two-column split
 * (`gardening-proposals-page.css`) — a real-Chromium proof that the `@media
 * (max-width: 720px)` rule collapses the grid to a single stacked column
 * (`bzh:narrow-viewport-tier-rule`): jsdom parses the query without ever
 * evaluating it, and gardening sits in the hub's mobile bottom tab bar, so the
 * narrow width is load-bearing, not incidental.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`) —
 * run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 *
 * Also proves the detail panel's own evidence rows (`proposal-panel.css`'s
 * `.pp-finding-locus`) wrap a long, unbroken locus rather than widening the panel
 * past its column at 390/320px — a real CSS layout claim jsdom cannot make — and
 * that above 720px the list and panel genuinely scroll independently rather than
 * as one shared unit (`gardening-page.css`'s own per-column split).
 */
const PROPOSAL = {
  proposal_id: 'gp_1',
  routine_name: 'comments',
  class: 'fix-the-source',
  title: 'Author a docstring standard covering change-history narration in module docstrings',
  body: 'Seventeen modules narrate their own change history in the docstring rather than stating the contract.',
  created_at: '2026-01-01T00:00:00Z',
  findings: ['fin_1'],
  closure: null,
};

const FINDING = {
  finding_id: 'fin_1',
  routine_name: 'comments',
  scope_slug: 'blizzard',
  class: 'stale-docstring',
  locus: 'src/blizzard/hub/store/internal/a-rather-long-module-path/invoice_ledger_reconciliation.py:142',
  summary: 'Module docstring narrates the change history rather than stating the contract.',
  state: 'live',
  live: true,
  observed_count: 1,
  last_seen_at: '2026-01-01T00:00:00Z',
};

/**
 * Stands in for `GardeningPage`'s own shell around the tab — `gardening-page.css`'s
 * `:host` flex column and its `.body` outlet wrapper, reproduced here so the height
 * chain the columns resolve against is the real one. The tab is mounted through the
 * real router because the docket and its detail are a parent/child route pair now
 * (`app.routes.ts`); a stubbed `ActivatedRoute` would leave the panel column empty
 * and there would be no second column to lay out.
 */
@Component({
  selector: 'app-test-proposals-shell-host',
  imports: [RouterOutlet],
  template: '<div class="shell-body"><router-outlet /></div>',
  styles: `
    :host {
      display: flex;
      flex-direction: column;
      flex: 1;
      min-height: 0;
    }
    .shell-body {
      flex: 1;
      min-height: 0;
      display: flex;
      flex-direction: column;
      padding: 8px;
    }
  `,
})
class TestProposalsShellHost {}

const routes: Routes = [
  {
    path: 'gardening/proposals',
    component: GardeningProposalsPage,
    children: [
      { path: '', component: GardeningProposalDetail },
      { path: ':proposalId', component: GardeningProposalDetail },
    ],
  },
];

async function render() {
  const stub = stubRequestClient(hubClient, (method, path) => {
    if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
    if (method === 'GET' && path === '/api/garden-proposals') return [PROPOSAL];
    if (method === 'GET' && path === '/api/findings/fin_1') return FINDING;
    return {};
  });
  await TestBed.configureTestingModule({
    imports: [TestProposalsShellHost],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      provideRouter(routes),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(TestProposalsShellHost);
  await TestBed.inject(Router).navigateByUrl('/gardening/proposals');
  await settle(fixture, 8);
  return { fixture, stub };
}

describe('gardening proposals page layout shell sweep (web:shell-sweep)', () => {
  it('sits the list beside the panel above 720px, and stacks them at 700px, 390px, and 320px', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    const { fixture, stub } = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(1280, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      let list = root.querySelector<HTMLElement>('.gp-list');
      let panel = root.querySelector<HTMLElement>('.gp-panel');
      expect(list, '1280px: no .gp-list in the DOM').not.toBeNull();
      expect(panel, '1280px: no .gp-panel in the DOM').not.toBeNull();
      expect(list!.getBoundingClientRect().top).toBe(panel!.getBoundingClientRect().top);
      expect(
        list!.getBoundingClientRect().right,
        '1280px: list and panel do not sit side by side',
      ).toBeLessThanOrEqual(panel!.getBoundingClientRect().left);

      for (const width of [700, 390, 320]) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        list = root.querySelector<HTMLElement>('.gp-list');
        panel = root.querySelector<HTMLElement>('.gp-panel');
        expect(list, `${width}px: no .gp-list in the DOM`).not.toBeNull();
        expect(panel, `${width}px: no .gp-panel in the DOM`).not.toBeNull();
        expect(
          list!.getBoundingClientRect().top,
          `${width}px: list and panel share a top — the grid did not collapse`,
        ).not.toBe(panel!.getBoundingClientRect().top);

        const layout = root.querySelector<HTMLElement>('.gp-layout')!;
        expect(
          layout.scrollWidth,
          `${width}px: layout overflows horizontally (${layout.scrollWidth} > ${layout.clientWidth})`,
        ).toBeLessThanOrEqual(layout.clientWidth);

        const locus = root.querySelector<HTMLElement>('.pp-finding-locus');
        expect(locus, `${width}px: no evidence row rendered in the panel`).not.toBeNull();
        expect(
          locus!.scrollWidth,
          `${width}px: the evidence locus overflows its own column instead of wrapping`,
        ).toBeLessThanOrEqual(panel!.clientWidth);

        // Stacked, each column sizes to its own content and `.body` scrolls the
        // whole tab (`gardening-page.css`) — a column must not still be clipped
        // to a bounded box of its own at this width.
        expect(
          list!.scrollHeight,
          `${width}px: .gp-list is still clipped to its own box below the collapse breakpoint`,
        ).toBeLessThanOrEqual(list!.clientHeight + 1);
      }
    } finally {
      root.remove();
      stub.restore();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});

describe('gardening proposals page independent-scroll shell sweep (web:shell-sweep)', () => {
  it('scrolls the list and the panel separately above 720px, without dragging one along with the other', async () => {
    const MANY_PROPOSALS = Array.from({ length: 30 }, (_, i) => ({
      ...PROPOSAL,
      proposal_id: `gp_${i}`,
      title: `${PROPOSAL.title} (${i})`,
    }));
    const LONG_BODY = Array.from({ length: 400 }, (_, i) => `Paragraph ${i} of a long proposal case.`).join(' ');

    const stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path === '/api/garden-proposals') {
        return [{ ...MANY_PROPOSALS[0], body: LONG_BODY }, ...MANY_PROPOSALS.slice(1)];
      }
      if (method === 'GET' && path === '/api/findings/fin_1') return FINDING;
      return {};
    });

    await TestBed.configureTestingModule({
      imports: [TestProposalsShellHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(TestProposalsShellHost);
    await TestBed.inject(Router).navigateByUrl('/gardening/proposals');
    await settle(fixture, 8);

    const root = fixture.nativeElement as HTMLElement;
    // A bounded ancestor, standing in for the real app shell's own
    // height:100%/overflow:hidden chain (`app-shell.css`'s `.shell`) that this
    // container mounts under in production — without it, the grid's own row
    // never resolves a definite height to bound and scroll its columns against.
    root.style.cssText = 'display: flex; flex-direction: column; height: 700px; min-height: 0; overflow: hidden;';
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(1280, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const list = root.querySelector<HTMLElement>('.gp-list')!;
      const panel = root.querySelector<HTMLElement>('.gp-panel')!;
      expect(list, 'no .gp-list in the DOM').not.toBeNull();
      expect(panel, 'no .gp-panel in the DOM').not.toBeNull();

      expect(
        list.scrollHeight,
        `the 30-proposal list never overflows its own box (${list.scrollHeight} <= ${list.clientHeight})`,
      ).toBeGreaterThan(list.clientHeight);
      expect(
        panel.scrollHeight,
        `the long proposal body never overflows the panel's own box (${panel.scrollHeight} <= ${panel.clientHeight})`,
      ).toBeGreaterThan(panel.clientHeight);

      list.scrollTop = list.scrollHeight;
      const listScrollTop = list.scrollTop;
      expect(listScrollTop, 'the list is clipped, not scrollable').toBeGreaterThan(0);
      expect(panel.scrollTop, 'scrolling the list moved the panel along with it').toBe(0);

      panel.scrollTop = panel.scrollHeight;
      expect(panel.scrollTop, 'the panel is clipped, not scrollable').toBeGreaterThan(0);
      expect(
        list.scrollTop,
        'scrolling the panel moved the list back off where the operator left it',
      ).toBe(listScrollTop);
    } finally {
      root.remove();
      stub.restore();
    }
  });
});
