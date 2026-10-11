import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { GardeningDoorway } from './gardening-doorway';

/**
 * The gardening doorway's layout (the tooled half of `blizzard-context:/verification/blizzard.md`'s
 * `web:shell-sweep` method) — a real, headless-Chromium proof that the project cards reflow to one column at phone
 * widths with no horizontal overflow, that a long project name or routine list wraps inside its card rather than
 * widening it, and that every card's way in stays on screen.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`) because it needs
 * `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const LONG = 'a-project-with-a-very-long-name-that-should-wrap-inside-its-card';
const PROJECTS = [
  { project_id: 'proj_1', slug: 'blizzard', name: 'Blizzard' },
  { project_id: 'proj_2', slug: 'long', name: LONG },
  { project_id: 'proj_3', slug: 'winter', name: 'Winter' },
];
const ROUTINES = ['garden-weekly', 'mutation-sweep', 'a-very-long-routine-name-that-should-wrap-too'].map(
  (name, i) => ({ name, routine_id: `rt_${i}`, project_id: 'proj_2', retired: false }),
);

function route(method: string, path: string): unknown {
  if (method !== 'GET') return {};
  if (path === '/api/projects') return { projects: PROJECTS };
  if (path === '/api/routines') return ROUTINES;
  if (path === '/api/scopes') return [{ slug: 'hub-api', project_id: 'proj_2', retired: false }];
  if (path === '/api/garden-proposals') {
    return { proposals: [{ proposal_id: 'gp_1', project_id: 'proj_2', closure: null }], next_cursor: null };
  }
  if (path === '/api/runners') return { runners: [] };
  return [];
}

describe('gardening doorway layout shell sweep (web:shell-sweep)', () => {
  let stub: RequestClientStub;
  afterEach(() => stub.restore());

  it.each([1280, 390, 320])('reflows every card with no horizontal overflow at %ipx', async (width) => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    stub = stubRequestClient(hubClient, route);
    await TestBed.configureTestingModule({
      imports: [GardeningDoorway],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(GardeningDoorway);
    const root = fixture.nativeElement as HTMLElement;
    root.style.display = 'flex';
    root.style.width = '100%';
    document.body.appendChild(root);

    try {
      await page.viewport(width, 900);
      await settle(fixture, 8);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const cards = root.querySelector<HTMLElement>('[data-testid="gardening-doorway"]')!;
      expect(cards).not.toBeNull();
      expect(
        cards.scrollWidth,
        `the cards overflow horizontally at ${width}px (${cards.scrollWidth} > ${cards.clientWidth})`,
      ).toBeLessThanOrEqual(cards.clientWidth);

      for (const project of PROJECTS) {
        const card = root.querySelector<HTMLElement>(`[data-testid="gardening-doorway-card-${project.slug}"]`)!;
        expect(card, `${project.slug}'s card`).not.toBeNull();
        expect(card.scrollWidth, `${project.slug}'s card overflows at ${width}px`).toBeLessThanOrEqual(card.clientWidth);
        const open = card.querySelector<HTMLElement>(`[data-testid="gardening-doorway-open-${project.slug}"]`)!;
        const rect = open.getBoundingClientRect();
        expect(rect.right, `${project.slug}'s way in leaves the viewport at ${width}px`).toBeLessThanOrEqual(width);
        expect(rect.width).toBeGreaterThan(0);
      }
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});
