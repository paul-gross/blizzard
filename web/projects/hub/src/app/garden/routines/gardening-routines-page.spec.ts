import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet, type Routes } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { ViewportService } from 'fleet';
import {
  OPERATOR_ME_RESPONSE,
  stubError,
  gardenGraphSummaryView,
  gardenRoutineView,
  settle,
  stubHubClient,
  type RequestClientStub,
} from 'fleet/testing';

import { HUB_PROJECT_FILTER } from '../../core/project-filter';
import { GardeningRoutinesPage } from './gardening-routines-page';

const ROUTINE = gardenRoutineView({
  routine_id: 'rtn_1',
  name: 'Nightly sweep',
  slug: 'nightly',
  graph_name: 'garden-routine',
  default_scope_slug: 'blizzard',
  default_model: ['claude-sonnet-5'],
  default_effort: 'medium',
  created_at: '2026-01-01T00:00:00Z',
});

const EFFECTIVE_GRAPH_SUMMARY = gardenGraphSummaryView();

/** Stands in for `GardeningRoutineDetail`, whose own behavior is
 * `gardening-routine-detail.spec.ts`'s. */
@Component({
  selector: 'app-test-routine-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '<span data-testid="routine-detail-stub"></span>',
})
class TestRoutineDetail {}

@Component({
  selector: 'app-test-routines-host',
  imports: [RouterOutlet],
  template: '<router-outlet />',
})
class TestRoutinesHost {}

/** The real route table's own shape for this tab (`app.routes.ts`), driven by the
 * real router — what is under test *is* that the parent reads its child's param
 * and survives the pick. */
const routes: Routes = [
  {
    path: 'gardening/routines',
    component: GardeningRoutinesPage,
    children: [
      { path: '', component: TestRoutineDetail },
      { path: ':routineSlug', component: TestRoutineDetail },
    ],
  },
];

/**
 * Exercises the `/gardening/routines` list container — the routine list, its
 * per-row blocked marking, its selection highlight, and the navigation a row pick
 * performs. The detail pane's own content is `gardening-routine-detail.spec.ts`'s.
 */
describe('GardeningRoutinesPage', () => {
  let stub: RequestClientStub;

  afterEach(() => stub?.restore());

  async function render(
    opts: { routines?: ReturnType<typeof gardenRoutineView>[]; graphs?: ReturnType<typeof gardenGraphSummaryView>[]; url?: string; mobile?: boolean; create?: () => unknown } = {},
  ) {
    const routines = opts.routines ?? [ROUTINE];
    const graphs = opts.graphs ?? [EFFECTIVE_GRAPH_SUMMARY];
    stub = stubHubClient({
      'GET /api/routines': () => routines,
      'GET /api/projects/{project}/routines': () => routines,
      'GET /api/graphs': () => graphs,
      'GET /api/cadences': () => [],
      'GET /api/me': () => OPERATOR_ME_RESPONSE,
      'GET /api/urgencies': (() => []) as never,
      'POST /api/projects/{project}/routines': (opts.create ?? (() => gardenRoutineView({ routine_id: 'rtn_new', name: 'weekly', slug: 'weekly' }))) as never,
    });
    await TestBed.configureTestingModule({
      imports: [TestRoutinesHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes),
        { provide: HUB_PROJECT_FILTER, useValue: signal('acme').asReadonly() },
      ],
    }).compileComponents();
    TestBed.inject(ViewportService).setOverride(opts.mobile ? 'mobile' : 'desktop');
    const fixture = TestBed.createComponent(TestRoutinesHost);
    const router = TestBed.inject(Router);
    await router.navigateByUrl(opts.url ?? '/gardening/routines');
    await settle(fixture, 12);
    const el = fixture.nativeElement as HTMLElement;
    const q = (testid: string) => el.querySelector<HTMLElement>(`[data-testid="${testid}"]`);
    return { fixture, router, el, q };
  }

  it('opens the first row on the bare route, beside its detail pane', async () => {
    const { el } = await render();

    expect(el.querySelector('[data-testid="routine-detail-stub"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-routine-row-nightly"]')?.classList.contains('selected')).toBe(
      true,
    );
  });

  it('highlights the row the child route names', async () => {
    const { el } = await render({ url: '/gardening/routines/nightly' });

    expect(el.querySelector('[data-testid="gardening-routine-row-nightly"]')?.classList.contains('selected')).toBe(
      true,
    );
  });

  it('lists the routine by its display name, keyed and highlighted by its slug', async () => {
    const { el } = await render({ url: '/gardening/routines/nightly' });

    const row = el.querySelector('[data-testid="gardening-routine-row-nightly"]');
    expect(row?.querySelector('.rt-name')?.textContent).toBe('Nightly sweep');
    expect(row?.classList.contains('selected')).toBe(true);
  });

  it('highlights no row for a route naming the display name rather than the slug', async () => {
    const { el } = await render({ url: '/gardening/routines/Nightly%20sweep', mobile: true });

    expect(el.querySelector('[data-testid="gardening-routine-row-nightly"]')?.classList.contains('selected')).toBe(
      false,
    );
  });

  it('keeps an unknown mobile routine route on its detail screen without a stale highlight', async () => {
    const { el } = await render({ url: '/gardening/routines/ghost', mobile: true });

    expect(el.querySelector('[data-testid="gardening-routine-row-nightly"]')?.classList.contains('selected')).toBe(
      false,
    );
    expect(el.querySelector('[data-testid="gardening-routines-back"]')).toBeTruthy();
  });

  it('marks a routine whose graph has no effective mint as blocked in the list', async () => {
    const { el } = await render({ graphs: [] });

    expect(el.querySelector('[data-testid="gardening-routine-row-nightly"]')?.textContent).toContain('blocked');
  });

  it('marks a retired routine in the list', async () => {
    const { el } = await render({ routines: [{ ...ROUTINE, retired: true }] });

    expect(el.querySelector('[data-testid="gardening-routine-row-nightly"]')?.textContent).toContain('retired');
  });

  it('navigates to the routine route when a routine row is picked', async () => {
    const { fixture, router, el } = await render();

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-row-nightly"]')!.click();
    await settle(fixture);

    expect(router.url).toBe('/gardening/routines/nightly');
  });

  it('renders its own empty state with no routines declared', async () => {
    const { el } = await render({ routines: [] });

    expect(el.querySelector('[data-testid="gardening-routines-empty"]')?.textContent).toContain(
      'No routines yet.',
    );
  });

  it('drills into a routine and back to the list in mobile mode', async () => {
    const { fixture, router, el } = await render({ mobile: true });

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-row-nightly"]')!.click();
    await settle(fixture);

    expect(router.url).toBe('/gardening/routines/nightly');
    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routines-back"]')!.click();
    await settle(fixture);

    expect(router.url).toBe('/gardening/routines');
  });

  async function type(fixture: Parameters<typeof settle>[0], q: (t: string) => HTMLElement | null, suffix: string, value: string) {
    const input = q(`gardening-routine-create-${suffix}`) as HTMLInputElement;
    input.value = value;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await settle(fixture);
  }

  it('creates a routine with the definition as typed and opens its detail', async () => {
    const { fixture, q, router } = await render();

    q('gardening-routine-new')!.click();
    await settle(fixture);
    await type(fixture, q, 'slug', 'weekly');
    await type(fixture, q, 'graph', 'garden-routine');
    await type(fixture, q, 'scope', 'blizzard');
    q('gardening-routine-create-save')!.click();
    await settle(fixture, 12);

    const calls = stub.forRoute('/api/projects/acme/routines', 'POST');
    expect(calls[0].body).toEqual({
      slug: 'weekly',
      name: null,
      graph_name: 'garden-routine',
      default_scope_slug: 'blizzard',
      default_urgency: null,
    });
    expect(calls[0].headers['if-match']).toBeUndefined();
    expect(router.url).toBe('/gardening/routines/weekly');
    expect(q('gardening-routine-create')).toBeNull();
  });

  it('refuses a blank create inline, sending nothing and focusing the first field in error', async () => {
    const { fixture, el, q } = await render();
    document.body.append(el);

    q('gardening-routine-new')!.click();
    await settle(fixture);
    q('gardening-routine-create-save')!.click();
    await settle(fixture, 12);

    expect(stub.forRoute('/api/projects/acme/routines', 'POST')).toHaveLength(0);
    for (const suffix of ['slug', 'graph', 'scope']) {
      expect(q(`gardening-routine-create-${suffix}`)!.closest('fleet-kit-field')!.textContent).toContain('Required');
    }
    expect(document.activeElement).toBe(q('gardening-routine-create-slug'));
    el.remove();
  });

  it('lands a malformed scope slug on the scope field, keeping the form and the draft', async () => {
    const { fixture, q } = await render({
      create: () => stubError(422, { type: 'urn:blizzard:problem:scope-slug-invalid', detail: 'scope slug is malformed' }),
    });

    q('gardening-routine-new')!.click();
    await settle(fixture);
    await type(fixture, q, 'slug', 'weekly');
    await type(fixture, q, 'graph', 'garden-routine');
    await type(fixture, q, 'scope', 'Bad Scope!');
    q('gardening-routine-create-save')!.click();
    await settle(fixture, 12);

    expect(q('gardening-routine-create')).not.toBeNull();
    expect(q('gardening-routine-create-scope')!.closest('fleet-kit-field')!.textContent).toContain('scope slug is malformed');
    expect((q('gardening-routine-create-slug') as HTMLInputElement).value).toBe('weekly');
    expect((q('gardening-routine-create-scope') as HTMLInputElement).value).toBe('Bad Scope!');
  });

  it('lands a definition refusal on the form line, keeping the draft', async () => {
    const { fixture, q } = await render({
      create: () => stubError(422, { type: 'urn:blizzard:problem:routine-definition-invalid', detail: 'the project already holds slug nightly' }),
    });

    q('gardening-routine-new')!.click();
    await settle(fixture);
    await type(fixture, q, 'slug', 'nightly');
    await type(fixture, q, 'graph', 'garden-routine');
    await type(fixture, q, 'scope', 'blizzard');
    q('gardening-routine-create-save')!.click();
    await settle(fixture, 12);

    expect(q('gardening-routine-create-line')?.textContent).toContain('the project already holds slug nightly');
    expect((q('gardening-routine-create-slug') as HTMLInputElement).value).toBe('nightly');
  });
});
