import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import {
  gardenProjectView,
  gardenProposalsPageView,
  gardenProposalView,
  gardenRoutineView,
  gardenScopeView,
  type RequestClientStub,
  runRowView,
  settle,
  stubHubClient,
} from 'fleet/testing';
import { page } from 'vitest/browser';

import { GardeningDoorway } from './gardening-doorway';

/**
 * The gardening doorway's layout (the tooled half of `blizzard-context:/verification/blizzard.md`'s
 * `web:shell-sweep` method) — a real, headless-Chromium proof that the project cards reflow to one column at phone
 * widths with no horizontal overflow, that no card narrows below the grid's 340px minimum column where the panel has
 * room for one, that a long project name or routine list wraps inside its card rather than widening it, that the last
 * run reads as a relative time, that every card's way in stays on screen, and that the loading and no-projects states
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
    'GET /api/scopes': () => [gardenScopeView({ slug: 'hub-api', project_id: 'proj_2' })],
    'GET /api/garden-proposals': () =>
      gardenProposalsPageView({ proposals: [gardenProposalView({ proposal_id: 'gp_1', project_id: 'proj_2' })] }),
    'GET /api/runners': () => ({ runners: [] }),
    'GET /api/runs': () => [
      runRowView({
        chunk_id: 'ch_1',
        project_id: 'proj_2',
        routine_name: 'garden-weekly',
        scope_slug: 'hub-api',
        mode: 'delta',
        minted_at: new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString(),
      }),
    ],
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
  root.style.display = 'flex';
  root.style.width = '100%';
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
        expect(card.tagName, `${project.slug}'s whole tile is the link`).toBe('A');
        const rect = card.getBoundingClientRect();
        expect(rect.right, `${project.slug}'s way in leaves the viewport at ${width}px`).toBeLessThanOrEqual(width);
        expect(rect.width).toBeGreaterThan(0);
        if (cards.clientWidth >= 340) {
          expect(card.getBoundingClientRect().width, `${project.slug}'s card is under 340px at ${width}px`).toBeGreaterThanOrEqual(340);
        }
      }

      const lastRun = root.querySelector<HTMLElement>('[data-testid="gardening-doorway-last-run-long"]')!;
      expect(lastRun.textContent).toContain('last run 3 days ago');
      expect(lastRun.querySelector('fleet-when')?.getAttribute('title'), 'the relative time keeps its tooltip').toBeTruthy();
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
      const panel = empty.root.querySelector<HTMLElement>('fleet-kit-panel')!;
      expect(shown.getBoundingClientRect().right).toBeLessThanOrEqual(panel.getBoundingClientRect().right);
      expect(empty.root.querySelector('[data-testid="gardening-doorway-loading"]')).toBeNull();
    } finally {
      empty.root.remove();
    }
  });
});
