import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet, type Routes } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient, ViewportService } from 'fleet';
import { OPERATOR_ME_RESPONSE, settle, stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { GardeningFindingsPage } from './gardening-findings-page';
import { GardeningRunsPage } from './gardening-runs-page';
import { GardeningScopesPage } from './gardening-scopes-page';

/**
 * The three gardening sub-tabs that arrived with the five-way tab split and had no
 * sweep of their own: Scopes (`.gs-layout`), Runs (`.gr-layout`), and Findings
 * (`.gf-layout`). Each declares the same `grid-template-columns: var(--master-list-
 * col) 1fr` master/detail split and the same mobile drill-down that Routines and
 * Proposals each carry a sweep for, so each owes the same proof
 * (`bzh:visual-change-needs-a-render`, `bzh:narrow-viewport-tier-rule`): jsdom
 * parses the media query without ever evaluating it, and gardening sits in the
 * hub's mobile bottom tab bar, so the narrow width is load-bearing.
 *
 * One file rather than three, unlike the per-page sweeps beside it, because the
 * claim really is one claim: the same grid, the same breakpoint, the same collapse,
 * differing only in the class prefix each page scopes it under. It is driven from a
 * table so a fourth page joining the split adds a row, not a copy — and so a page
 * silently dropping the shared layout fails here rather than being quietly absent.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`) —
 * run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const SCOPES = [
  { slug: 'blizzard', description: 'the hub, runner, CLI and board', retired: false, created_at: '2026-01-01T00:00:00Z' },
  { slug: 'web', description: 'the Angular workspace', retired: false, created_at: '2026-01-01T00:00:00Z' },
  ...Array.from({ length: 38 }, (_, i) => ({
    slug: `scope-${i + 1}`,
    description: `Additional scope ${i + 1}`,
    retired: false,
    created_at: '2026-01-01T00:00:00Z',
  })),
];

const ROUTINES = [
  {
    routine_id: 'rtn_1',
    name: 'nightly',
    graph_name: 'garden-routine',
    default_scope_slug: 'blizzard',
    default_model: ['claude-sonnet-5'],
    default_effort: 'medium',
    created_at: '2026-01-01T00:00:00Z',
  },
];

const RUNS = [
  {
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
    routine_name: 'nightly',
    scope_slug: 'blizzard',
    mode: 'full',
    minted_at: '2026-01-10T00:00:00Z',
    outcome: 'done',
    escalation: null,
    delivered: [
      {
        finding_set_id: 'fins_1',
        revisions: { blizzard: '4ba7ef06d9f1c2b3a4e5f60718293a4b5c6d7e8f' },
        measurement: '3 findings',
        added_count: 1,
        observed_count: 2,
        gone_count: 0,
      },
    ],
  },
];

/** A deliberately long, unbroken locus — the value that would widen a column past
 * its track if the list did not wrap it. */
const FINDINGS = [
  {
    finding_id: 'fnd_1',
    routine_name: 'nightly',
    scope_slug: 'blizzard',
    class: 'stale-docstring',
    locus: 'src/blizzard/hub/store/internal/a-rather-long-module-path/invoice_ledger_reconciliation.py:142',
    summary: 'Module docstring narrates the change history rather than stating the contract.',
    state: 'live',
    live: true,
    observed_count: 1,
    last_seen_at: '2026-01-10T00:00:00Z',
    facts: [{ kind: 'add', recorded_at: '2026-01-01T00:00:00Z' }],
  },
];

/**
 * Stands in for `GardeningPage`'s own shell around the tab — `gardening-page.css`'s
 * `:host` flex column and its `.body` outlet wrapper, reproduced here so the height
 * chain each page's columns resolve against is the real one.
 */
@Component({
  selector: 'app-test-gardening-grid-host',
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
class TestGardeningGridHost {}

@Component({
  selector: 'app-test-gardening-detail',
  template: '<span data-testid="gardening-detail-stub"></span>',
})
class TestGardeningDetail {}

const routes: Routes = [
  {
    path: 'gardening/scopes',
    component: GardeningScopesPage,
    children: [
      { path: '', component: TestGardeningDetail },
      { path: ':scopeSlug', component: TestGardeningDetail },
    ],
  },
  {
    path: 'gardening/runs',
    component: GardeningRunsPage,
    children: [
      { path: '', component: TestGardeningDetail },
      { path: ':chunkId', component: TestGardeningDetail },
    ],
  },
  {
    path: 'gardening/findings',
    component: GardeningFindingsPage,
    children: [
      { path: '', component: TestGardeningDetail },
      { path: ':findingId', component: TestGardeningDetail },
    ],
  },
];

async function render(url: string) {
  const stub = stubRequestClient(hubClient, (method, path) => {
    if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
    if (method === 'GET' && path === '/api/scopes') return SCOPES;
    if (method === 'GET' && path === '/api/routines') return ROUTINES;
    if (method === 'GET' && path === '/api/runs') return RUNS;
    if (method === 'GET' && path === '/api/findings') return FINDINGS;
    if (method === 'GET' && path === '/api/garden-proposals') return [];
    return {};
  });
  await TestBed.configureTestingModule({
    imports: [TestGardeningGridHost],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      provideRouter(routes),
    ],
  }).compileComponents();
  const viewport = TestBed.inject(ViewportService);
  viewport.setOverride('desktop');
  const fixture = TestBed.createComponent(TestGardeningGridHost);
  const router = TestBed.inject(Router);
  await router.navigateByUrl(url);
  await settle(fixture, 12);
  return { fixture, router, stub, viewport };
}

/** One row per sub-tab: the route to mount and the three class names its own CSS
 * scopes the shared grid under. */
const PAGES = [
  {
    name: 'scopes',
    url: '/gardening/scopes',
    selectedUrl: '/gardening/scopes/blizzard',
    layout: '.gs-layout',
    left: '.gs-left',
    right: '.gs-right',
    back: '[data-testid="gardening-scopes-back"]',
  },
  {
    name: 'runs',
    url: '/gardening/runs',
    selectedUrl: '/gardening/runs/ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
    layout: '.gr-layout',
    left: '.gr-list',
    right: '.gr-detail',
    back: '[data-testid="gardening-runs-back"]',
  },
  {
    name: 'findings',
    url: '/gardening/findings',
    selectedUrl: '/gardening/findings/fnd_1',
    layout: '.gf-layout',
    left: '.gf-list',
    right: '.gf-detail',
    back: '[data-testid="gardening-findings-back"]',
  },
] as const;

describe('gardening sub-tab layout shell sweep (web:shell-sweep)', () => {
  for (const spec of PAGES) {
    it(`${spec.name}: sits list beside detail on desktop and drills from list to detail on mobile`, async () => {
      const { fixture, router, stub, viewport } = await render(spec.url);
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);
      await fixture.whenStable();

      try {
        await page.viewport(1280, 800);
        await settle(fixture);

        let left = root.querySelector<HTMLElement>(spec.left);
        let right = root.querySelector<HTMLElement>(spec.right);
        expect(left, `1280px: no ${spec.left} in the DOM`).not.toBeNull();
        expect(right, `1280px: no ${spec.right} in the DOM`).not.toBeNull();
        expect(left!.getBoundingClientRect().top).toBe(right!.getBoundingClientRect().top);
        expect(
          left!.getBoundingClientRect().right,
          `1280px: ${spec.left} and ${spec.right} do not sit side by side`,
        ).toBeLessThanOrEqual(right!.getBoundingClientRect().left);

        for (const width of [740, 700, 390, 320]) {
          await page.viewport(width, 800);
          viewport.setOverride('mobile');
          await settle(fixture);

          left = root.querySelector<HTMLElement>(spec.left);
          right = root.querySelector<HTMLElement>(spec.right);
          expect(left, `${width}px: no ${spec.left} in the DOM`).not.toBeNull();
          expect(right, `${width}px: no ${spec.right} in the DOM`).not.toBeNull();
          expect(
            left!.getBoundingClientRect().width,
            `${width}px: ${spec.left} is not the visible list screen`,
          ).toBeGreaterThan(0);
          expect(right!.getBoundingClientRect().width, `${width}px: ${spec.right} is visible on the bare route`).toBe(0);

          await router.navigateByUrl(spec.selectedUrl);
          await settle(fixture, 12);

          expect(left!.getBoundingClientRect().width, `${width}px: ${spec.left} remains visible after selection`).toBe(0);
          expect(
            right!.getBoundingClientRect().width,
            `${width}px: ${spec.right} is not the visible detail screen`,
          ).toBeGreaterThan(0);
          const back = root.querySelector<HTMLAnchorElement>(spec.back);
          expect(back, `${width}px: no mobile Back control`).not.toBeNull();

          const layout = root.querySelector<HTMLElement>(spec.layout)!;
          expect(
            layout.scrollWidth,
            `${width}px: layout overflows horizontally (${layout.scrollWidth} > ${layout.clientWidth})`,
          ).toBeLessThanOrEqual(layout.clientWidth);

          back!.click();
          await settle(fixture);
          expect(router.url).toBe(spec.url);
        }
      } finally {
        viewport.setOverride('auto');
        root.remove();
        stub.restore();
        await page.viewport(1280, 800);
      }
    });
  }

  it('opens detail at the top and restores a long list scroll position on Back', async () => {
    const { fixture, router, stub, viewport } = await render('/gardening/scopes');
    const root = fixture.nativeElement as HTMLElement;
    root.style.cssText = 'display: flex; flex-direction: column; height: 600px; min-height: 0; overflow: hidden;';
    document.body.appendChild(root);

    try {
      await page.viewport(390, 800);
      viewport.setOverride('mobile');
      await settle(fixture);

      const listScroller = root.querySelector<HTMLElement>('.gs-left .p-body')!;
      listScroller.scrollTop = listScroller.scrollHeight;
      const listScrollTop = listScroller.scrollTop;
      expect(listScrollTop, 'the long mobile list never became scrollable').toBeGreaterThan(0);

      await router.navigateByUrl('/gardening/scopes/scope-38');
      await settle(fixture);

      const detailScroller = root.querySelector<HTMLElement>('.gs-right .p-body')!;
      const back = root.querySelector<HTMLAnchorElement>('[data-testid="gardening-scopes-back"]')!;
      expect(detailScroller.scrollTop, 'detail inherited the list scroll position').toBe(0);
      expect(back.getBoundingClientRect().top).toBeGreaterThanOrEqual(detailScroller.getBoundingClientRect().top);
      expect(back.getBoundingClientRect().bottom).toBeLessThanOrEqual(detailScroller.getBoundingClientRect().bottom);

      back.click();
      await settle(fixture);

      expect(router.url).toBe('/gardening/scopes');
      expect(listScroller.scrollTop, 'Back did not restore the list scroll position').toBe(listScrollTop);
    } finally {
      viewport.setOverride('auto');
      root.remove();
      stub.restore();
      await page.viewport(1280, 800);
    }
  });
});
