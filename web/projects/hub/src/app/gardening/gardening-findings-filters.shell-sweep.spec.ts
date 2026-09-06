import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet, type Routes } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, settle, stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { GardeningFindingDetail } from './gardening-finding-detail';
import { GardeningFindingsPage } from './gardening-findings-page';

/**
 * The findings tab's own filter row (blizzard#486, phase 2 of 2): widened to every
 * routine and every scope, the four chip rows (routine, scope, class, state) now
 * carry a leading "All" option apiece, and a row from a widened bucket carries its
 * own routine/scope alongside the class and ref (`finding-list.css`'s `.fl-routine`/
 * `.fl-scope`). A real, headless-Chromium proof
 * (`blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method) that
 * neither genuinely overflows at the phone widths gardening is reached at
 * (`bzh:narrow-viewport-tier-rule`) — jsdom lays out the grid/flex chains involved
 * without ever checking whether a long routine/scope pair actually overflows them.
 * Also proves a long, unbroken class name still shrinks-and-ellipsizes on
 * `.fl-class`'s own line, alongside `.fl-ref`, rather than wrapping the ref onto a
 * second line once `.fl-routine`/`.fl-scope` render too (review:F2) — jsdom would
 * happily lay out `flex-wrap: wrap` without ever exercising the hypothetical-size
 * wrap decision the bug lived in.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`) —
 * run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
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
  {
    routine_id: 'rtn_2',
    name: 'weekly',
    graph_name: 'garden-routine',
    default_scope_slug: 'web',
    default_model: ['claude-sonnet-5'],
    default_effort: 'medium',
    created_at: '2026-01-01T00:00:00Z',
  },
];

const SCOPES = [
  { slug: 'blizzard', description: 'the hub, runner, CLI and board', retired: false, created_at: '2026-01-01T00:00:00Z' },
  { slug: 'web', description: 'the Angular workspace', retired: false, created_at: '2026-01-01T00:00:00Z' },
];

/** Two findings from two distinct routines and two distinct scopes, so the
 * disambiguation markup (blizzard#486) has something genuine to render. `fnd_1`'s
 * `class` is deliberately a long, unbroken 40+ character run — proves `.fl-class`
 * shrinks-and-ellipsizes on its own line rather than pushing `.fl-ref` onto a
 * second line once `.fl-routine`/`.fl-scope` also render (review:F2). */
const FINDINGS = [
  {
    finding_id: 'fnd_1',
    routine_name: 'nightly',
    scope_slug: 'blizzard',
    class: 'stale-docstring-with-a-genuinely-long-unbroken-class-name',
    locus: 'src/a.py:1',
    summary: 'docstring narrates a removed parameter',
    state: 'live',
    live: true,
    observed_count: 1,
    last_seen_at: '2026-01-10T00:00:00Z',
  },
  {
    finding_id: 'fnd_2',
    routine_name: 'weekly',
    scope_slug: 'web',
    class: 'unused-import',
    locus: 'src/b.ts:9',
    summary: 'import no longer referenced',
    state: 'live',
    live: true,
    observed_count: 1,
    last_seen_at: '2026-01-10T00:00:00Z',
  },
];

/** `gardening-page-grids.shell-sweep.spec.ts`'s own shell stand-in, reproduced here
 * so the height chain `.gf-layout` resolves against is the real one. */
@Component({
  selector: 'app-test-gardening-findings-filters-host',
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
class TestGardeningFindingsFiltersHost {}

const routes: Routes = [
  {
    path: 'gardening/findings',
    component: GardeningFindingsPage,
    children: [
      { path: '', component: GardeningFindingDetail },
      { path: ':findingId', component: GardeningFindingDetail },
    ],
  },
];

async function render() {
  const stub = stubRequestClient(hubClient, (method, path) => {
    if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
    if (method === 'GET' && path === '/api/scopes') return SCOPES;
    if (method === 'GET' && path === '/api/routines') return ROUTINES;
    if (method === 'GET' && path === '/api/findings') return FINDINGS;
    if (method === 'GET' && path === '/api/garden-proposals') return [];
    return {};
  });
  await TestBed.configureTestingModule({
    imports: [TestGardeningFindingsFiltersHost],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      provideRouter(routes),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(TestGardeningFindingsFiltersHost);
  await TestBed.inject(Router).navigateByUrl('/gardening/findings');
  await settle(fixture, 12);
  return { fixture, stub };
}

describe('gardening findings filter row and row disambiguation shell sweep (web:shell-sweep, blizzard#486)', () => {
  it.each([390, 320])('renders all four filter chip rows and a disambiguated row with no horizontal overflow at %ipx', async (width) => {
    const { fixture, stub } = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(width, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const filters = root.querySelector<HTMLElement>('.gf-filters')!;
      expect(filters).not.toBeNull();
      expect(
        filters.scrollWidth,
        `${width}px: .gf-filters overflows horizontally (${filters.scrollWidth} > ${filters.clientWidth})`,
      ).toBeLessThanOrEqual(filters.clientWidth);

      for (const testid of [
        'gardening-findings-routine-all',
        'gardening-findings-scope-all',
        'gardening-finding-class-all',
        'gardening-finding-state-all',
      ]) {
        expect(root.querySelector(`[data-testid="${testid}"]`), `${width}px: missing ${testid}`).not.toBeNull();
      }

      const row = root.querySelector<HTMLElement>('[data-testid="gardening-finding-row-fnd_1"]')!;
      expect(row).not.toBeNull();
      expect(row.querySelector('.fl-routine')?.textContent?.trim()).toBe('nightly');
      expect(row.querySelector('.fl-scope')?.textContent?.trim()).toBe('blizzard');
      expect(
        row.scrollWidth,
        `${width}px: the disambiguated row overflows horizontally (${row.scrollWidth} > ${row.clientWidth})`,
      ).toBeLessThanOrEqual(row.clientWidth);

      // The long, unbroken class name (review:F2) must shrink-and-ellipsize on
      // `.fl-class`'s own line rather than wrap `.fl-ref` onto a second line — proven
      // by the two sharing the same `top`, not merely by the row's own overall
      // scrollWidth, which the wrap bug above didn't move.
      const cls = row.querySelector<HTMLElement>('.fl-class')!;
      const ref = row.querySelector<HTMLElement>('.fl-ref')!;
      const topDelta = Math.abs(ref.getBoundingClientRect().top - cls.getBoundingClientRect().top);
      expect(
        topDelta,
        `${width}px: .fl-class and .fl-ref no longer share the same line (top delta ${topDelta}px)`,
      ).toBeLessThan(6);
    } finally {
      root.remove();
      stub.restore();
      await page.viewport(1280, 800);
    }
  });
});
