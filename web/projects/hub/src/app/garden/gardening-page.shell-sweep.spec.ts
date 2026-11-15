import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, RouterOutlet, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import type { GardenProposalView } from 'fleet';
import { gardenProjectView, gardenProposalView, settle, stubHubClient } from 'fleet/testing';
import { commands, page } from 'vitest/browser';

import { ProjectLens, provideProjectLens } from '../core/project-lens';
import { gardeningDoorwayGuard } from './garden-lens.guard';
import { GardeningPage } from './gardening-page';

/**
 * A project's garden page (the tooled half of `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep`
 * method) — a real, headless-Chromium proof that a bare `/p/{project}/gardening` lands on the Proposals tab; that the
 * gardening strip wears the kit's sub-strip — 30px tall on `--panel-deep`, its active tab cyan on `--overlay-30` with a
 * `--cyan-dim` underline — beneath the app's own strip; that the Proposals count turns `--amber-hi` while any proposal
 * waits and stays plain at zero; and that at phone widths all six tabs wrap inside the page with 44px targets.
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

const PROJECTS = [gardenProjectView({ project_id: 'proj_b', slug: 'blizzard', name: 'Blizzard' })];
const waiting = (id: string) => gardenProposalView({ proposal_id: id, project_id: 'proj_b' });

async function render(proposals: GardenProposalView[]) {
  const stub = stubHubClient({
    'GET /api/projects': () => ({ projects: PROJECTS }),
    'GET /api/runners': () => ({ runners: [] }),
    'GET /api/garden-proposals': () => ({ proposals, next_cursor: null }),
    'GET /api/projects/{project}/garden-proposals': () => ({ proposals, next_cursor: null }),
    'GET /api/scopes': () => [],
    'GET /api/projects/{project}/scopes': () => [],
    'GET /api/routines': () => [],
    'GET /api/projects/{project}/routines': () => [],
    'GET /api/runs': () => [],
    'GET /api/projects/{project}/runs': () => [],
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
  return { fixture, root, router, stub };
}

describe('gardening page sub-strip shell sweep (web:shell-sweep)', () => {
  beforeAll(loadDesignTokens);

  it.each([1280, 390, 320])('lands on Proposals under the sub-strip, its waiting count amber, at %ipx', async (width) => {
    await page.viewport(width, 800);
    const { root, router, stub } = await render([waiting('gp_1'), waiting('gp_2')]);

    try {
      await new Promise((resolve) => requestAnimationFrame(resolve));
      expect(router.url).toBe('/p/blizzard/gardening/proposals');

      const strip = root.querySelector<HTMLElement>('[data-testid="gardening-subnav"]')!;
      expect(strip.classList.contains('sub')).toBe(true);
      if (width >= 768) expect(getComputedStyle(strip).height).toBe('30px');
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
      if (width < 768) {
        const tabs = [...strip.querySelectorAll<HTMLElement>('[fleetKitTab]')];
        expect(tabs).toHaveLength(6);
        expect(getComputedStyle(strip).flexWrap).toBe('wrap');
        expect(strip.scrollWidth).toBeLessThanOrEqual(strip.clientWidth);
        const bounds = strip.getBoundingClientRect();
        for (const tab of tabs) {
          const box = tab.getBoundingClientRect();
          expect(box.height).toBeGreaterThanOrEqual(44);
          expect(box.left).toBeGreaterThanOrEqual(bounds.left);
          expect(box.right).toBeLessThanOrEqual(bounds.right + 1);
          expect(box.bottom).toBeLessThanOrEqual(bounds.bottom + 1);
        }
        expect(tabs.at(-1)!.getBoundingClientRect().top).toBeGreaterThan(tabs[0].getBoundingClientRect().top);
      }
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

  it.each([320, 390, 1280])('keeps tabs under the global project lens without a local selector at %ipx', async (width) => {
    await page.viewport(width, 800);
    const { fixture, root, router, stub } = await render([]);
    try {
      expect(root.querySelector('[data-testid="gardening-crumbs"]')).toBeNull();
      expect(root.querySelector('[data-testid="gardening-crumb-project"]')).toBeNull();
      await TestBed.inject(ProjectLens).switchTo('winter', '/gardening/proposals');
      for (let i = 0; i < 8; i += 1) {
        fixture.detectChanges();
        await new Promise((resolve) => setTimeout(resolve, 0));
      }
      fixture.detectChanges();
      expect(router.url).toBe('/p/winter/gardening/proposals');
      const scopes = root.querySelector<HTMLAnchorElement>('[data-testid="gardening-tab-scopes"]')!;
      expect(scopes.getAttribute('href')).toBe('/p/winter/gardening/scopes');
      scopes.click();
      for (let i = 0; i < 8; i += 1) {
        fixture.detectChanges();
        await new Promise((resolve) => setTimeout(resolve, 0));
      }
      fixture.detectChanges();
      expect(router.url).toBe('/p/winter/gardening/scopes');
      expect(root.querySelector('[data-testid="gardening-tab-scopes"]')?.getAttribute('aria-current')).toBe('page');
    } finally {
      root.remove();
      stub.restore();
    }
  });
});
