import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { AppNav } from './app-nav';

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
