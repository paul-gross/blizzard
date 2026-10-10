import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { type MeResponse, hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { TAB_TENANT_KEY } from '../../core/auth/tenant-context';
import { AppNav } from './app-nav';
import { MobileTitlebar } from './mobile-titlebar';

@Component({ selector: 'app-test-sweep-view', template: '' })
class TestView {}

/** A tenant with more projects, and longer names, than the tab row has room for. */
const PROJECTS = [
  'Blizzard',
  'Celestial Navigation Platform',
  'Northwind Interplanetary Logistics',
  'Glacier',
  'Snowball Reference Implementation',
  'Avalanche',
  'Permafrost Archive and Recovery',
].map((name, i) => ({
  project_id: `proj_${i}`,
  slug: name.toLowerCase().split(' ')[0],
  name,
  description: '',
  revision: 1,
  created_at: '2026-10-01T00:00:00Z',
}));

// The desktop tab row's widths: a wide monitor down to the narrowest desktop the board renders.
const WIDTHS = [1400, 1280, 1150, 1000, 900, 800, 768, 700];

/**
 * The project lens control in the hub's view-tab row, under `web:shell-sweep`: a real, headless-Chromium proof that a
 * tenant with many long-named projects never pushes the tab row past the viewport — the chips scroll inside the
 * control — while every route tab and the lens's own choice stay on screen and hit-testable, at every desktop width.
 */
describe('project lens control sweep (web:shell-sweep)', () => {
  it('keeps the tab row inside the viewport, its tabs and the chosen lens on screen, at every width', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    window.addEventListener('error', onError);

    const stub = stubRequestClient(hubClient, (_method, path) => (path === '/api/projects' ? { projects: PROJECTS } : {}));
    await TestBed.configureTestingModule({
      imports: [AppNav],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient()),
        provideRouter([
          { path: 'p/:projectSlug', children: [{ path: '**', component: TestView }] },
          { path: '**', component: TestView },
        ]),
      ],
    }).compileComponents();
    await TestBed.inject(Router).navigateByUrl('/p/blizzard/board');
    const fixture = TestBed.createComponent(AppNav);
    fixture.componentRef.setInput('showAdmin', true);
    fixture.componentRef.setInput('lensEnabled', true);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();
    await new Promise((resolve) => setTimeout(resolve, 50));
    await fixture.whenStable();

    try {
      expect(root.querySelectorAll('[data-testid^="project-lens-"]').length, 'All and every project').toBe(
        PROJECTS.length + 1,
      );
      for (const width of WIDTHS) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));
        const label = `width=${width}`;

        const nav = root.querySelector<HTMLElement>('[data-testid="app-nav"]')!;
        expect(nav.getBoundingClientRect().right, `${label}: the tab row is past the viewport`).toBeLessThanOrEqual(
          window.innerWidth,
        );
        expect(document.documentElement.scrollWidth, `${label}: the page scrolls sideways`).toBeLessThanOrEqual(
          window.innerWidth,
        );

        for (const testid of ['nav-board', 'nav-graphs', 'nav-events', 'nav-gardening', 'nav-admin', 'project-lens-all']) {
          const el = root.querySelector<HTMLElement>(`[data-testid="${testid}"]`);
          expect(el, `${label}: no ${testid}`).not.toBeNull();
          const rect = el!.getBoundingClientRect();
          expect(rect.width, `${label}: ${testid} has no width`).toBeGreaterThan(0);
          expect(rect.right, `${label}: ${testid} is past the viewport (${rect.right})`).toBeLessThanOrEqual(
            window.innerWidth,
          );
          const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
          expect(el!.contains(hit), `${label}: ${testid}'s center hit-tests to something else`).toBe(true);
        }

        const chosen = root.querySelector<HTMLElement>('[data-testid="project-lens-blizzard"]')!;
        expect(chosen.getAttribute('aria-pressed'), `${label}: the lens's choice is not marked`).toBe('true');
      }
    } finally {
      stub.restore();
      root.remove();
      window.removeEventListener('error', onError);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});

/** A caller whose tenant has a long name, so the mobile titlebar's tenant and project lines compete for the room. */
const LONG_TENANT_ME: MeResponse = {
  ...OPERATOR_ME_RESPONSE,
  tenants: [{ tenant_id: 'ten_acme', name: 'Acme Interplanetary Holdings', role: 'admin', permissions: ['fleet:view'] }],
};

// Phone widths, down to the narrowest the board renders.
const PHONE_WIDTHS = [430, 390, 360, 320];

/**
 * The mobile titlebar under a project lens, under `web:shell-sweep`: the bar names the lensed project beside the
 * tenant, and a long tenant and a long project name both truncate inside the bar — the project's name keeps a visible
 * share of it, and the live dot and the menu stay on screen and hit-testable — at every phone width.
 */
describe('mobile titlebar lens sweep (web:shell-sweep)', () => {
  it('names the lensed project beside the tenant without pushing the dot or the menu off screen', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    window.addEventListener('error', onError);

    sessionStorage.setItem(TAB_TENANT_KEY, 'ten_acme');
    const stub = stubRequestClient(hubClient, (_method, path) => {
      if (path === '/api/me') return LONG_TENANT_ME;
      if (path === '/api/projects') return { projects: PROJECTS };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [MobileTitlebar],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient()),
        provideRouter([
          { path: 'p/:projectSlug', children: [{ path: '**', component: TestView }] },
          { path: '**', component: TestView },
        ]),
      ],
    }).compileComponents();
    await TestBed.inject(Router).navigateByUrl('/p/northwind/board');
    const fixture = TestBed.createComponent(MobileTitlebar);
    fixture.componentRef.setInput('live', true);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();
    await new Promise((resolve) => setTimeout(resolve, 50));
    await fixture.whenStable();

    try {
      const detail = root.querySelector<HTMLElement>('[data-testid="mobile-titlebar-detail"]');
      expect(detail?.textContent?.trim(), 'the lensed project is not named').toBe('Northwind Interplanetary Logistics');
      for (const width of PHONE_WIDTHS) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));
        const label = `width=${width}`;

        const bar = root.querySelector<HTMLElement>('[data-testid="mobile-titlebar"]')!;
        expect(bar.getBoundingClientRect().right, `${label}: the bar is past the viewport`).toBeLessThanOrEqual(
          window.innerWidth,
        );
        expect(document.documentElement.scrollWidth, `${label}: the page scrolls sideways`).toBeLessThanOrEqual(
          window.innerWidth,
        );
        expect(detail!.getBoundingClientRect().width, `${label}: the project's name has no room`).toBeGreaterThan(40);

        for (const testid of ['mobile-titlebar-livedot', 'mobile-titlebar-menu']) {
          const el = root.querySelector<HTMLElement>(`[data-testid="${testid}"]`);
          expect(el, `${label}: no ${testid}`).not.toBeNull();
          const rect = el!.getBoundingClientRect();
          expect(rect.right, `${label}: ${testid} is past the viewport (${rect.right})`).toBeLessThanOrEqual(
            window.innerWidth,
          );
          const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
          expect(el!.contains(hit), `${label}: ${testid}'s center hit-tests to something else`).toBe(true);
        }
      }
    } finally {
      stub.restore();
      root.remove();
      sessionStorage.clear();
      window.removeEventListener('error', onError);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});
