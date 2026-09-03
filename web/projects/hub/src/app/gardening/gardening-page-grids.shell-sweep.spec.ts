import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet, type Routes } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, settle, stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { GardeningFindingDetail } from './gardening-finding-detail';
import { GardeningFindingsPage } from './gardening-findings-page';
import { GardeningRunDetail } from './gardening-run-detail';
import { GardeningRunsPage } from './gardening-runs-page';
import { GardeningScopeDetail } from './gardening-scope-detail';
import { GardeningScopesPage } from './gardening-scopes-page';

/**
 * The three gardening sub-tabs that arrived with the five-way tab split and had no
 * sweep of their own: Scopes (`.gs-layout`), Runs (`.gr-layout`), and Findings
 * (`.gf-layout`). Each declares the same `grid-template-columns: var(--master-list-
 * col) 1fr` master/detail split and the same `@media (max-width: 720px)` collapse
 * that Routines and Proposals each carry a sweep for, so each owes the same proof
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

const routes: Routes = [
  {
    path: 'gardening/scopes',
    component: GardeningScopesPage,
    children: [
      { path: '', component: GardeningScopeDetail },
      { path: ':scopeSlug', component: GardeningScopeDetail },
    ],
  },
  {
    path: 'gardening/runs',
    component: GardeningRunsPage,
    children: [
      { path: '', component: GardeningRunDetail },
      { path: ':chunkId', component: GardeningRunDetail },
    ],
  },
  {
    path: 'gardening/findings',
    component: GardeningFindingsPage,
    children: [
      { path: '', component: GardeningFindingDetail },
      { path: ':findingId', component: GardeningFindingDetail },
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
  const fixture = TestBed.createComponent(TestGardeningGridHost);
  await TestBed.inject(Router).navigateByUrl(url);
  await settle(fixture, 12);
  return { fixture, stub };
}

/** One row per sub-tab: the route to mount and the three class names its own CSS
 * scopes the shared grid under. */
const PAGES = [
  { name: 'scopes', url: '/gardening/scopes', layout: '.gs-layout', left: '.gs-left', right: '.gs-right' },
  { name: 'runs', url: '/gardening/runs', layout: '.gr-layout', left: '.gr-list', right: '.gr-detail' },
  { name: 'findings', url: '/gardening/findings', layout: '.gf-layout', left: '.gf-list', right: '.gf-detail' },
] as const;

describe('gardening sub-tab layout shell sweep (web:shell-sweep)', () => {
  for (const spec of PAGES) {
    it(`${spec.name}: sits the list beside the detail above 720px, and stacks them at 700px, 390px, and 320px`, async () => {
      const { fixture, stub } = await render(spec.url);
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);
      await fixture.whenStable();

      try {
        await page.viewport(1280, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        let left = root.querySelector<HTMLElement>(spec.left);
        let right = root.querySelector<HTMLElement>(spec.right);
        expect(left, `1280px: no ${spec.left} in the DOM`).not.toBeNull();
        expect(right, `1280px: no ${spec.right} in the DOM`).not.toBeNull();
        expect(left!.getBoundingClientRect().top).toBe(right!.getBoundingClientRect().top);
        expect(
          left!.getBoundingClientRect().right,
          `1280px: ${spec.left} and ${spec.right} do not sit side by side`,
        ).toBeLessThanOrEqual(right!.getBoundingClientRect().left);

        for (const width of [700, 390, 320]) {
          await page.viewport(width, 800);
          await new Promise((resolve) => requestAnimationFrame(resolve));

          left = root.querySelector<HTMLElement>(spec.left);
          right = root.querySelector<HTMLElement>(spec.right);
          expect(left, `${width}px: no ${spec.left} in the DOM`).not.toBeNull();
          expect(right, `${width}px: no ${spec.right} in the DOM`).not.toBeNull();
          expect(
            left!.getBoundingClientRect().top,
            `${width}px: ${spec.left} and ${spec.right} share a top — the grid did not collapse`,
          ).not.toBe(right!.getBoundingClientRect().top);

          const layout = root.querySelector<HTMLElement>(spec.layout)!;
          expect(
            layout.scrollWidth,
            `${width}px: layout overflows horizontally (${layout.scrollWidth} > ${layout.clientWidth})`,
          ).toBeLessThanOrEqual(layout.clientWidth);
        }
      } finally {
        root.remove();
        stub.restore();
        await page.viewport(1280, 800);
      }
    });
  }
});
