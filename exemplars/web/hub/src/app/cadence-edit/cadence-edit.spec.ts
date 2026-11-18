// exemplar: bzh:frontend-edit-baseline, bzh:frontend-mutation-settles-on-refresh — the container's edit asserted.

import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { gardenCadenceView, type RequestClientStub, settle, stubError, stubHubClient } from 'fleet/testing';
import { hubCadenceEditListKey } from '../core/query-keys';
import { CadenceEdit } from './cadence-edit';

const EVENING = gardenCadenceView({ slug: 'evening', revision: 7 });

describe('CadenceEdit', () => {
  let stub: RequestClientStub;
  let read: typeof EVENING;
  afterEach(() => stub?.restore());
  beforeEach(() => (read = EVENING));

  async function render(patch: () => unknown = () => EVENING) {
    stub = stubHubClient({
      'GET /api/cadences': () => [read],
      'PATCH /api/cadences/{cadence_id}': patch as never,
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'cadences/:cadenceSlug', component: CadenceEdit }]),
      ],
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/cadences/evening');
    await settle(harness.fixture, 12);
    const el = harness.routeNativeElement as HTMLElement;
    const q = (testid: string) => el.querySelector<HTMLElement>(`[data-testid="${testid}"]`);
    const click = async (testid: string) => {
      q(testid)!.click();
      await settle(harness.fixture, 12);
    };
    const type = async (testid: string, value: string) => {
      const input = q(testid) as HTMLInputElement;
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await settle(harness.fixture, 12);
    };
    return { harness, q, click, type };
  }

  it('saves only what changed against the revision it started from, after a refresh moved the read', async () => {
    const { harness, q, click, type } = await render();

    await click('cadence-edit-start');
    await type('cadence-edit-form-name', 'Evening sweep');
    read = { ...EVENING, revision: 8 };
    await TestBed.inject(QueryClient).invalidateQueries({ queryKey: hubCadenceEditListKey });
    await settle(harness.fixture, 12);
    expect((q('cadence-edit-form-name') as HTMLInputElement).value).toBe('Evening sweep');

    await click('cadence-edit-form-save');

    const calls = stub.forRoute('/api/cadences/cad_evening', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].headers['if-match']).toBe('7');
    expect(calls[0].body).toEqual({ name: 'Evening sweep' });
    expect(q('cadence-edit-form')).toBeNull();
  });

  it('keeps the form and the typed draft on a revision conflict', async () => {
    const { q, click, type } = await render(() =>
      stubError(409, { type: 'urn:blizzard:problem:revision-conflict', detail: 'the cadence changed since it was read' }),
    );

    await click('cadence-edit-start');
    await type('cadence-edit-form-cron', '0 21 * * *');
    await click('cadence-edit-form-save');

    expect(stub.forRoute('/api/cadences/cad_evening', 'PATCH')).toHaveLength(1);
    expect(q('cadence-edit-form-line')?.textContent).toContain('the cadence changed since it was read');
    expect((q('cadence-edit-form-cron') as HTMLInputElement).value).toBe('0 21 * * *');
  });

  it('refuses an invalid edit inline, sending nothing', async () => {
    const { q, click, type } = await render();

    await click('cadence-edit-start');
    await type('cadence-edit-form-zone', '');
    await click('cadence-edit-form-save');

    expect(stub.forRoute('/api/cadences/cad_evening', 'PATCH')).toHaveLength(0);
    expect(q('cadence-edit-form-zone')!.closest('fleet-kit-field')!.textContent).toContain(
      'A schedule needs both a cron and a zone',
    );
  });
});
