import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { HUB_PROJECT_FILTER } from 'fleet/shell';
import {
  OPERATOR_ME_RESPONSE,
  gardenCadenceErrandView,
  gardenCadenceView,
  gardenChoreView,
  type RequestClientStub,
  settle,
  stubError,
  stubHubClient,
} from 'fleet/testing';

import { GardeningCadenceDetail } from './gardening-cadence-detail';

@Component({ selector: 'app-test-cadence-detail-host', imports: [RouterOutlet], template: '<router-outlet />' })
class TestCadenceDetailHost {}

const EVENING = gardenCadenceView({
  slug: 'evening',
  revision: 7,
  chores: [gardenChoreView(), gardenChoreView({ chore_id: 'chr_2', key: 'odd', routine: 'docs', scopes: ['web'], promote: false })],
});

/** Exercises the cadence detail container: the slug resolved, its errands read, and every write's request. */
describe('GardeningCadenceDetail', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  async function render(opts: { url?: string; patch?: () => unknown } = {}) {
    stub = stubHubClient({
      'GET /api/me': () => OPERATOR_ME_RESPONSE,
      'GET /api/projects/{project}/cadences': () => [EVENING],
      'GET /api/cadences/{cadence_id}/errands': () => [
        gardenCadenceErrandView({ scope: 'blizzard' }),
        gardenCadenceErrandView({ chore: 'odd', scope: 'web', state: 'due' }),
      ],
      'PATCH /api/cadences/{cadence_id}': (opts.patch ?? (() => EVENING)) as never,
      'POST /api/cadences/{cadence_id}/retire': () => ({ ...EVENING, retired: true }),
    });
    await TestBed.configureTestingModule({
      imports: [TestCadenceDetailHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([
          { path: 'gardening/cadences', component: GardeningCadenceDetail },
          { path: 'gardening/cadences/:cadenceSlug', component: GardeningCadenceDetail },
        ]),
        { provide: HUB_PROJECT_FILTER, useValue: signal('acme').asReadonly() },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(TestCadenceDetailHost);
    await TestBed.inject(Router).navigateByUrl(opts.url ?? '/gardening/cadences/evening');
    await settle(fixture, 12);
    const el = fixture.nativeElement as HTMLElement;
    const q = (testid: string) => el.querySelector<HTMLElement>(`[data-testid="${testid}"]`);
    const click = async (testid: string) => {
      q(testid)!.click();
      await settle(fixture, 12);
    };
    return { fixture, el, q, click };
  }

  it('rests on its own empty state with nothing selected', async () => {
    const { q } = await render({ url: '/gardening/cadences' });
    expect(q('gardening-cadence-panel-empty')).not.toBeNull();
    expect(stub.forRoute('/api/cadences/cad_evening/errands', 'GET')).toHaveLength(0);
  });

  it("resolves the slug and reads that cadence's errands by id", async () => {
    const { q } = await render();
    expect(q('gardening-cadence-panel')).not.toBeNull();
    expect(stub.forRoute('/api/cadences/cad_evening/errands', 'GET')).toHaveLength(1);
    expect(q('gardening-cadence-errand-nightly-blizzard')).not.toBeNull();
    expect(q('gardening-cadence-errand-odd-web')?.textContent).toContain('due');
  });

  it('removes a chore by sending the rest of the whole list against the read revision', async () => {
    const { click } = await render();

    await click('gardening-cadence-chore-remove-nightly');
    await click('confirm-dialog-confirm');

    const [call] = stub.forRoute('/api/cadences/cad_evening', 'PATCH');
    expect(call.headers['if-match']).toBe('7');
    expect(call.body).toEqual({
      chores: [{ key: 'odd', routine: 'docs', scopes: ['web'], mode: 'full', promote: false, note: null }],
    });
  });

  it('renders a stale-revision refusal', async () => {
    const { click, q } = await render({ patch: () => stubError(409, { detail: 'the cadence changed since it was read' }) });

    await click('gardening-cadence-chore-remove-odd');
    await click('confirm-dialog-confirm');

    expect(q('gardening-cadence-panel-error')?.textContent).toContain('the cadence changed since it was read');
  });

  it('retires against the read revision', async () => {
    const { click } = await render();

    await click('gardening-cadence-panel-retire');
    await click('confirm-dialog-confirm');

    const [call] = stub.forRoute('/api/cadences/cad_evening/retire', 'POST');
    expect(call.headers['if-match']).toBe('7');
  });
});
