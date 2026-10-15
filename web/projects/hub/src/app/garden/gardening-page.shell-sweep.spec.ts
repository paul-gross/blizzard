import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, RouterOutlet, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { settle, stubRequestClient } from 'fleet/testing';
import { commands, page } from 'vitest/browser';

import { ProjectLens, provideProjectLens } from '../core/project-lens';
import { gardeningDoorwayGuard } from './garden-lens.guard';
import { GardeningPage } from './gardening-page';

/**
 * A project's garden page (the tooled half of `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep`
 * method) — a real, headless-Chromium proof that a bare `/p/{project}/gardening` lands on the Proposals tab; that the
 * gardening strip wears the kit's sub-strip — 30px tall on `--panel-deep`, its active tab cyan on `--overlay-30` with a
 * `--cyan-dim` underline — beneath the app's own strip; that the Proposals count turns `--amber-hi` while any proposal
 * waits and stays plain at zero; and that at a phone width the strip scrolls inside itself rather than widening the
 * page, its active tab still on screen.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`) because it needs
 * `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
}

/** The computed value a design token resolves to for `property`, read off a probe element. */
function token(name: string, property: 'color' | 'backgroundColor' = 'color'): string {
  const probe = document.createElement('span');
  probe.style[property] = `var(${name})`;
  document.body.appendChild(probe);
  const value = getComputedStyle(probe)[property];
  probe.remove();
  return value;
}

@Component({ selector: 'app-test-garden-tab', template: '' })
class TestGardenTab {}

@Component({ selector: 'app-test-garden-sweep-host', imports: [RouterOutlet], template: '<router-outlet />' })
class TestGardenSweepHost {}

const PROJECTS = [{ project_id: 'proj_b', slug: 'blizzard', name: 'Blizzard' }];
const waiting = (id: string) => ({
  proposal_id: id,
  project_id: 'proj_b',
  routine_name: 'comments',
  class: 'x',
  body: 'b',
  created_at: '2026-01-01T00:00:00Z',
  findings: [],
});

async function render(proposals: readonly unknown[]) {
  const stub = stubRequestClient(hubClient, (method, path) => {
    if (method !== 'GET') return {};
    if (path === '/api/projects') return { projects: PROJECTS };
    if (path === '/api/runners') return { runners: [] };
    if (path.endsWith('/garden-proposals')) return { proposals, next_cursor: null };
    if (path.endsWith('/scopes') || path.endsWith('/routines') || path.endsWith('/runs')) return [];
    return {};
  });
  await TestBed.configureTestingModule({
    imports: [TestGardenSweepHost],
    providers: [
      provideZonelessChangeDetection(),
      provideRouter([
        { path: 'p/:projectSlug/gardening', pathMatch: 'full', canActivate: [gardeningDoorwayGuard], component: TestGardenTab },
        { path: 'p/:projectSlug/gardening', component: GardeningPage, children: [{ path: ':tab', component: TestGardenTab }] },
      ]),
      provideProjectLens(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(TestGardenSweepHost);
  const root = fixture.nativeElement as HTMLElement;
  root.style.cssText = 'display: flex; flex-direction: column; width: 100%; height: 600px;';
  document.body.appendChild(root);
  const router = TestBed.inject(Router);
  TestBed.inject(ProjectLens).settle(true);
  await router.navigateByUrl('/p/blizzard/gardening');
  await settle(fixture, 8);
  return { root, router, stub };
}

describe('gardening page sub-strip shell sweep (web:shell-sweep)', () => {
  beforeAll(loadDesignTokens);

  it.each([1280, 390])('lands on Proposals under the sub-strip, its waiting count amber, at %ipx', async (width) => {
    await page.viewport(width, 800);
    const { root, router, stub } = await render([waiting('gp_1'), waiting('gp_2')]);

    try {
      await new Promise((resolve) => requestAnimationFrame(resolve));
      expect(router.url).toBe('/p/blizzard/gardening/proposals');

      const strip = root.querySelector<HTMLElement>('[data-testid="gardening-subnav"]')!;
      expect(strip.classList.contains('sub')).toBe(true);
      expect(getComputedStyle(strip).height).toBe('30px');
      expect(getComputedStyle(strip).backgroundColor).toBe(token('--panel-deep', 'backgroundColor'));

      const active = root.querySelector<HTMLElement>('[data-testid="gardening-tab-proposals"]')!;
      expect(active.classList.contains('active')).toBe(true);
      expect(active.getAttribute('aria-current')).toBe('page');
      const style = getComputedStyle(active);
      expect(style.color).toBe(token('--cyan'));
      expect(style.backgroundColor).toBe(token('--overlay-30', 'backgroundColor'));
      expect(style.boxShadow).toContain(token('--cyan-dim'));
      const idle = getComputedStyle(root.querySelector<HTMLElement>('[data-testid="gardening-tab-scopes"]')!);
      expect(idle.color).toBe(token('--label'));

      const count = root.querySelector<HTMLElement>('[data-testid="gardening-tab-count-proposals"]')!;
      expect(count.textContent).toBe('2');
      expect(getComputedStyle(count).color).toBe(token('--amber-hi'));
      expect(getComputedStyle(root.querySelector<HTMLElement>('[data-testid="gardening-tab-count-scopes"]')!).color).toBe(
        token('--snow'),
      );

      expect(document.documentElement.scrollWidth, `the page widens at ${width}px`).toBeLessThanOrEqual(width);
      expect(strip.getBoundingClientRect().right).toBeLessThanOrEqual(width);
      strip.scrollLeft = strip.scrollWidth;
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const rect = active.getBoundingClientRect();
      expect(rect.width).toBeGreaterThan(0);
      expect(rect.right, `the active tab cannot be reached at ${width}px`).toBeLessThanOrEqual(width + 1);
    } finally {
      root.remove();
      stub.restore();
    }
  });

  it('draws the Proposals count plain when nothing waits', async () => {
    await page.viewport(1280, 800);
    const { root, stub } = await render([]);
    try {
      const count = root.querySelector<HTMLElement>('[data-testid="gardening-tab-count-proposals"]')!;
      expect(count.textContent).toBe('0');
      expect(getComputedStyle(count).color).toBe(token('--snow'));
    } finally {
      root.remove();
      stub.restore();
    }
  });
});
