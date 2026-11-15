import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import {
  gardenProjectView,
  gardenProposalsPageView,
  gardenProposalView,
  gardenRoutineView,
  type RequestClientStub,
  settle,
  stubHubClient,
} from 'fleet/testing';
import { page } from 'vitest/browser';

import { GardeningDoorway } from './gardening-doorway';

/**
 * The gardening doorway's layout (the tooled half of `blizzard-context:/verification/blizzard.md`'s
 * `web:shell-sweep` method) — a real, headless-Chromium proof that the project cards reflow to one column at phone
 * widths with no horizontal overflow, that a long project name wraps inside its card rather than widening it,
 * that every card's way in stays on screen, and that the loading and no-projects states
 * each render on their own.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`) because it needs
 * `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const LONG = 'a-project-with-a-very-long-name-that-should-wrap-inside-its-card';
const PROJECTS = [
  gardenProjectView({ project_id: 'proj_1', slug: 'blizzard', name: 'Blizzard' }),
  gardenProjectView({ project_id: 'proj_2', slug: 'long', name: LONG }),
  gardenProjectView({ project_id: 'proj_3', slug: 'winter', name: 'Winter' }),
];
const ROUTINES = ['garden-weekly', 'mutation-sweep', 'a-very-long-routine-name-that-should-wrap-too'].map((name, i) =>
  gardenRoutineView({ name, routine_id: `rt_${i}`, project_id: 'proj_2' }),
);

type DoorwayRoutes = Parameters<typeof stubHubClient>[0];

function routes(): DoorwayRoutes {
  return {
    'GET /api/projects': () => ({ projects: PROJECTS }),
    'GET /api/routines': () => ROUTINES,
    'GET /api/cadences': () => [],
    'GET /api/garden-proposals': () =>
      gardenProposalsPageView({ proposals: [gardenProposalView({ proposal_id: 'gp_1', project_id: 'proj_2' })] }),
    'GET /api/runners': () => ({ runners: [] }),
  };
}

async function mount(overrides: DoorwayRoutes = {}) {
  stubbed = stubHubClient({ ...routes(), ...overrides });
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
  // Leave the padded host auto-sized, as it is in the shell; 100% content width adds padding outside the viewport.
  document.body.appendChild(root);
  return { fixture, root };
}

let stubbed: RequestClientStub | null = null;

describe('gardening doorway layout shell sweep (web:shell-sweep)', () => {
  let stub: RequestClientStub;
  afterEach(() => stub.restore());

  it.each([1280, 390, 320])('reflows every card with no horizontal overflow at %ipx', async (width) => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    stub = stubHubClient(routes());
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
    // Match the auto-sized shell host rather than imposing a 100% content-box width.
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
        expect(card.tagName, `${project.slug}'s whole tile is the link`).toBe('A');
        const rect = card.getBoundingClientRect();
        expect(rect.right, `${project.slug}'s way in leaves the viewport at ${width}px`).toBeLessThanOrEqual(width);
        expect(rect.width).toBeGreaterThan(0);
      }

      expect(root.querySelector('[data-testid="gardening-doorway-card-long"]')?.textContent).toContain('3 routines · 1 pending proposal');
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });

  it.each([1280, 390])('renders loading until the reads answer, then the no-projects state, at %ipx', async (width) => {
    await page.viewport(width, 900);
    const never = new Promise<never>(() => undefined);
    const loading = await mount({ 'GET /api/projects': () => never });
    stub = stubbed!;
    try {
      await settle(loading.fixture, 4);
      expect(loading.root.querySelector('[data-testid="gardening-doorway-loading"]')).not.toBeNull();
      expect(loading.root.querySelector('[data-testid="gardening-doorway-empty"]')).toBeNull();
    } finally {
      loading.root.remove();
      stub.restore();
    }

    TestBed.resetTestingModule();
    const empty = await mount({ 'GET /api/projects': () => ({ projects: [] }) });
    stub = stubbed!;
    try {
      await settle(empty.fixture, 8);
      const shown = empty.root.querySelector<HTMLElement>('[data-testid="gardening-doorway-empty"]')!;
      expect(shown).not.toBeNull();
      expect(shown.getBoundingClientRect().right).toBeLessThanOrEqual(width);
      expect(empty.root.querySelector('[data-testid="gardening-doorway-loading"]')).toBeNull();
    } finally {
      empty.root.remove();
    }
  });
});
