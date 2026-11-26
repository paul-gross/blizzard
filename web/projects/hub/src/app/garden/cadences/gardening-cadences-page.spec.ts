import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet, type Routes } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { ViewportService } from 'fleet/viewport';
import { provideFlags } from 'fleet/shell';
import {
  OPERATOR_ME_RESPONSE,
  gardenCadenceView,
  gardenChoreView,
  type RequestClientStub,
  settle,
  stubError,
  stubHubClient,
} from 'fleet/testing';

import { hubFlagsRead } from '../../core/flags.query';
import { GardeningCadencesPage } from './gardening-cadences-page';
import { CADENCES_FLAG_OFF_LINE } from './gardening-cadences-page.model';
import { HUB_PROJECT_FILTER } from '../../core/project-filter';

/** Stands in for `GardeningCadenceDetail`, whose own behavior is `gardening-cadence-detail.spec.ts`'s. */
@Component({
  selector: 'app-test-cadence-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '<span data-testid="cadence-detail-stub"></span>',
})
class TestCadenceDetail {}

@Component({ selector: 'app-test-cadences-host', imports: [RouterOutlet], template: '<router-outlet />' })
class TestCadencesHost {}

const routes: Routes = [
  {
    path: 'gardening/cadences',
    component: GardeningCadencesPage,
    children: [
      { path: '', component: TestCadenceDetail },
      { path: ':cadenceSlug', component: TestCadenceDetail },
    ],
  },
];

const EVENING = gardenCadenceView({
  slug: 'evening',
  name: 'Evening sweep',
  chores: [gardenChoreView()],
  errand_counts: { due: 1, running: 0, waiting: 2, stopped: 0 },
});

/** Exercises the `/gardening/cadences` list container: its rows, the flag-off line, and the create form. */
describe('GardeningCadencesPage', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  async function render(opts: { cadences?: readonly unknown[]; flagOn?: boolean; create?: () => unknown } = {}) {
    stub = stubHubClient({
      'GET /api/me': () => OPERATOR_ME_RESPONSE,
      'GET /api/flags': () => ({ flags: { cadences: opts.flagOn ?? false }, overridden: [] }),
      'GET /api/projects/{project}/cadences': () => (opts.cadences ?? [EVENING]) as never,
      'POST /api/projects/{project}/cadences': (opts.create ?? (() => gardenCadenceView({ slug: 'weekly' }))) as never,
    });
    await TestBed.configureTestingModule({
      imports: [TestCadencesHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes),
        provideFlags(hubFlagsRead),
        { provide: HUB_PROJECT_FILTER, useValue: signal('acme').asReadonly() },
      ],
    }).compileComponents();
    TestBed.inject(ViewportService).setOverride('desktop');
    const fixture = TestBed.createComponent(TestCadencesHost);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/gardening/cadences');
    await settle(fixture, 12);
    const el = fixture.nativeElement as HTMLElement;
    const q = (testid: string) => el.querySelector<HTMLElement>(`[data-testid="${testid}"]`);
    return { fixture, router, el, q };
  }

  it("lists the project's cadences and opens the first on desktop", async () => {
    const { q, router } = await render();

    expect(q('gardening-cadence-row-evening')?.textContent).toContain('Evening sweep');
    expect(q('gardening-cadence-counts-evening')?.textContent).toContain('1 chore');
    expect(router.url).toBe('/gardening/cadences/evening');
    expect(stub.forRoute('/api/projects/acme/cadences', 'GET')).toHaveLength(1);
  });

  it('says cadences mint nothing while the flag is off', async () => {
    const { q } = await render({ flagOn: false });
    expect(q('gardening-cadences-flag-off')?.textContent?.trim()).toBe(CADENCES_FLAG_OFF_LINE);
  });

  it('shows no flag-off line while the flag is on', async () => {
    const { q } = await render({ flagOn: true });
    expect(q('gardening-cadence-row-evening')).not.toBeNull();
    expect(q('gardening-cadences-flag-off')).toBeNull();
  });

  it('creates a cadence and opens its detail', async () => {
    const { fixture, q, router } = await render();

    q('gardening-cadence-new')!.click();
    await settle(fixture);
    const slug = q('gardening-cadence-create-slug') as HTMLInputElement;
    slug.value = 'weekly';
    slug.dispatchEvent(new Event('input', { bubbles: true }));
    const spacing = q('gardening-cadence-create-spacing') as HTMLInputElement;
    spacing.value = '7d';
    spacing.dispatchEvent(new Event('input', { bubbles: true }));
    await settle(fixture);
    q('gardening-cadence-create-save')!.click();
    await settle(fixture, 12);

    expect(stub.forRoute('/api/projects/acme/cadences', 'POST')[0].body).toEqual({ slug: 'weekly', spacing: '7d' });
    expect(router.url).toBe('/gardening/cadences/weekly');
    expect(q('gardening-cadence-create')).toBeNull();
  });

  it('marks the slug required and refuses a blank create inline, sending nothing and focusing the slug', async () => {
    const { fixture, el, q } = await render();
    document.body.append(el);

    q('gardening-cadence-new')!.click();
    await settle(fixture);
    const field = (testid: string) => q(testid)!.closest('fleet-kit-field')!;
    expect(field('gardening-cadence-create-slug').querySelector('.req')).not.toBeNull();
    expect(field('gardening-cadence-create-name').querySelector('.req')).toBeNull();

    q('gardening-cadence-create-save')!.click();
    await settle(fixture, 12);

    expect(stub.forRoute('/api/projects/acme/cadences', 'POST')).toHaveLength(0);
    expect(field('gardening-cadence-create-slug').textContent).toContain('Required');
    expect(q('gardening-cadence-create-line')?.textContent).toContain('State a schedule, a spacing, or both');
    expect(document.activeElement).toBe(q('gardening-cadence-create-slug'));
    el.remove();
  });

  it('shows a rule failure once the field is touched', async () => {
    const { fixture, q } = await render();

    q('gardening-cadence-new')!.click();
    await settle(fixture);
    const cron = q('gardening-cadence-create-cron') as HTMLInputElement;
    cron.value = '0 20 * *';
    cron.dispatchEvent(new Event('input', { bubbles: true }));
    await settle(fixture);
    const field = cron.closest('fleet-kit-field')!;
    expect(field.textContent).not.toContain('Needs five fields');
    cron.dispatchEvent(new Event('blur'));
    await settle(fixture);
    expect(field.textContent).toContain('Needs five fields');
  });

  it('lands a slug already taken on the slug field, keeping the form and the draft', async () => {
    const { fixture, q } = await render({
      create: () => stubError(422, { type: 'urn:blizzard:problem:cadence-slug-taken', detail: 'the project already holds slug evening' }),
    });

    q('gardening-cadence-new')!.click();
    await settle(fixture);
    for (const [suffix, value] of [['slug', 'evening'], ['spacing', '7d']]) {
      const input = q(`gardening-cadence-create-${suffix}`) as HTMLInputElement;
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    await settle(fixture);
    q('gardening-cadence-create-save')!.click();
    await settle(fixture, 12);

    expect(stub.forRoute('/api/projects/acme/cadences', 'POST')).toHaveLength(1);
    expect(q('gardening-cadence-create')).not.toBeNull();
    expect(q('gardening-cadence-create-slug')!.closest('fleet-kit-field')!.textContent).toContain(
      'the project already holds slug evening',
    );
    expect(q('gardening-cadence-create-line')).toBeNull();
    expect((q('gardening-cadence-create-slug') as HTMLInputElement).value).toBe('evening');
    expect((q('gardening-cadence-create-spacing') as HTMLInputElement).value).toBe('7d');
  });

  it('words a failure no field owns on the form line', async () => {
    const { fixture, q } = await render({ create: () => stubError(500, { detail: 'boom' }) });

    q('gardening-cadence-new')!.click();
    await settle(fixture);
    const input = q('gardening-cadence-create-slug') as HTMLInputElement;
    input.value = 'weekly';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    const spacing = q('gardening-cadence-create-spacing') as HTMLInputElement;
    spacing.value = '7d';
    spacing.dispatchEvent(new Event('input', { bubbles: true }));
    await settle(fixture);
    q('gardening-cadence-create-save')!.click();
    await settle(fixture, 12);

    expect(q('gardening-cadence-create-line')?.textContent).toContain('boom');
  });
});
