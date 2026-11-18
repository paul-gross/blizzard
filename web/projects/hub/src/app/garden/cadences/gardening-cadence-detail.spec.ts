import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
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
import { HUB_PROJECT_FILTER } from '../../core/project-filter';

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

  async function render(
    opts: { url?: string; patch?: () => unknown; errands?: () => unknown; cadences?: () => unknown[] } = {},
  ) {
    stub = stubHubClient({
      'GET /api/me': () => OPERATOR_ME_RESPONSE,
      'GET /api/projects/{project}/cadences': (opts.cadences ?? (() => [EVENING])) as never,
      'GET /api/cadences/{cadence_id}/errands': (opts.errands ?? (() => [
        gardenCadenceErrandView({ scope: 'blizzard' }),
        gardenCadenceErrandView({ chore: 'odd', scope: 'web', state: 'due' }),
      ])) as never,
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
    const queryClient = TestBed.inject(QueryClient);
    const fixture = TestBed.createComponent(TestCadenceDetailHost);
    await TestBed.inject(Router).navigateByUrl(opts.url ?? '/gardening/cadences/evening');
    await settle(fixture, 12);
    const el = fixture.nativeElement as HTMLElement;
    const q = (testid: string) => el.querySelector<HTMLElement>(`[data-testid="${testid}"]`);
    const click = async (testid: string) => {
      q(testid)!.click();
      await settle(fixture, 12);
    };
    const type = async (testid: string, value: string) => {
      const input = q(testid) as HTMLInputElement;
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await settle(fixture, 12);
    };
    return { fixture, el, q, click, type, queryClient };
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

  it('forwards an errands read failure without hiding cadence controls', async () => {
    const { q } = await render({ errands: () => stubError(500, { detail: 'failed' }) });
    expect(q('gardening-cadence-errands-error-nightly')).not.toBeNull();
    expect(q('gardening-cadence-errands-error-odd')).not.toBeNull();
    expect(q('gardening-cadence-chore-add')).not.toBeNull();
  });

  it('shows empty only for chores without matching errands', async () => {
    const { q } = await render({ errands: () => [gardenCadenceErrandView({ chore: 'nightly', scope: 'a' })] });
    expect(q('gardening-cadence-errand-nightly-a')).not.toBeNull();
    expect(q('gardening-cadence-errands-empty-odd')).not.toBeNull();
  });

  it('removes a chore by sending the rest of the whole list against the read revision', async () => {
    const { click } = await render();

    await click('gardening-cadence-chore-remove-nightly');
    await click('confirm-dialog-confirm');

    const [call] = stub.forRoute('/api/cadences/cad_evening', 'PATCH');
    expect(call.headers['if-match']).toBe('7');
    expect(call.body).toEqual({
      chores: [{ key: 'odd', routine: 'docs', scopes: ['web'], mode: 'full', promote: false, note: null, urgency: null }],
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

  describe('the cadence edit', () => {
    let read: typeof EVENING;
    const cadences = () => [read];
    beforeEach(() => (read = EVENING));

    it('saves only what changed against the revision it started from, after a refresh moved the read', async () => {
      const { click, type, q, queryClient, fixture } = await render({ cadences });

      await click('gardening-cadence-panel-edit');
      await type('gardening-cadence-edit-name', 'Evening sweep');
      read = { ...EVENING, revision: 8 };
      await queryClient.invalidateQueries({ queryKey: ['hub', 'cadences'] });
      await settle(fixture, 12);
      expect((q('gardening-cadence-edit-name') as HTMLInputElement).value).toBe('Evening sweep');

      await click('gardening-cadence-edit-save');

      const calls = stub.forRoute('/api/cadences/cad_evening', 'PATCH');
      expect(calls).toHaveLength(1);
      expect(calls[0].headers['if-match']).toBe('7');
      expect(calls[0].body).toEqual({ name: 'Evening sweep' });
      expect(q('gardening-cadence-edit')).toBeNull();
    });

    it('keeps the form and the typed draft on a revision conflict, sending once, and restarts from a fresh read', async () => {
      let conflict = true;
      const { click, type, q } = await render({
        cadences,
        patch: () =>
          conflict
            ? stubError(409, { type: 'urn:blizzard:problem:revision-conflict', detail: 'the cadence changed since it was read' })
            : EVENING,
      });

      await click('gardening-cadence-panel-edit');
      await type('gardening-cadence-edit-cron', '0 21 * * *');
      read = { ...EVENING, revision: 8 };
      await click('gardening-cadence-edit-save');

      expect(stub.forRoute('/api/cadences/cad_evening', 'PATCH')).toHaveLength(1);
      expect(q('gardening-cadence-edit-line')?.textContent).toContain('the cadence changed since it was read');
      expect((q('gardening-cadence-edit-cron') as HTMLInputElement).value).toBe('0 21 * * *');
      expect(q('gardening-cadence-panel-error')?.textContent ?? '').not.toContain('changed since');

      conflict = false;
      await click('gardening-cadence-edit-cancel');
      expect(q('gardening-cadence-edit')).toBeNull();
      await click('gardening-cadence-panel-edit');
      expect((q('gardening-cadence-edit-cron') as HTMLInputElement).value).toBe('0 20 * * *');
      await type('gardening-cadence-edit-cron', '0 21 * * *');
      await click('gardening-cadence-edit-save');

      const calls = stub.forRoute('/api/cadences/cad_evening', 'PATCH');
      expect(calls).toHaveLength(2);
      expect(calls[1].headers['if-match']).toBe('8');
      expect(calls[1].body).toEqual({ schedule: { cron: '0 21 * * *', zone: 'America/Chicago' } });
      expect(q('gardening-cadence-edit')).toBeNull();
    });

    it('refuses an invalid edit inline, sending nothing', async () => {
      const { click, type, q } = await render();

      await click('gardening-cadence-panel-edit');
      await type('gardening-cadence-edit-zone', '');
      await click('gardening-cadence-edit-save');

      expect(stub.forRoute('/api/cadences/cad_evening', 'PATCH')).toHaveLength(0);
      expect(q('gardening-cadence-edit-zone')!.closest('fleet-kit-field')!.textContent).toContain(
        'A schedule needs both a cron and a zone',
      );
    });

    it('ends when a chore form opens', async () => {
      const { click, q } = await render();

      await click('gardening-cadence-panel-edit');
      await click('gardening-cadence-chore-add');

      expect(q('gardening-cadence-edit')).toBeNull();
      expect(q('gardening-chore-add-key')).not.toBeNull();
    });
  });

  describe('the chore forms', () => {
    let read: typeof EVENING;
    const cadences = () => [read];
    beforeEach(() => (read = EVENING));

    it('refuses a blank routine on its field, sending nothing, and focuses it', async () => {
      const { click, type, q } = await render();

      await click('gardening-cadence-chore-add');
      await type('gardening-chore-add-key', 'weekly');
      await click('gardening-chore-add-save');

      expect(stub.forRoute('/api/cadences/cad_evening', 'PATCH')).toHaveLength(0);
      expect(q('gardening-chore-add-routine')!.closest('fleet-kit-field')!.textContent).toContain('required');
      expect(document.activeElement).toBe(q('gardening-chore-add-routine'));
    });

    it('adds a chore against the revision it opened on, and closes on success', async () => {
      const { click, type, q, fixture, queryClient } = await render({ cadences });

      await click('gardening-cadence-chore-add');
      read = { ...EVENING, revision: 9 };
      await queryClient.invalidateQueries({ queryKey: ['hub', 'cadences'] });
      await settle(fixture, 12);
      await type('gardening-chore-add-key', 'weekly');
      await type('gardening-chore-add-routine', 'deep');
      await click('gardening-chore-add-save');

      const [call] = stub.forRoute('/api/cadences/cad_evening', 'PATCH');
      expect(call.headers['if-match']).toBe('7');
      expect((call.body as { chores: { key: string }[] }).chores.map((c) => c.key)).toEqual(['nightly', 'odd', 'weekly']);
      expect(q('gardening-chore-add')).toBeNull();
    });

    it('keeps an edit open with its draft on a raced save, sending once, and Cancel restarts from the read', async () => {
      const { click, type, q } = await render({
        patch: () => stubError(409, { type: 'urn:blizzard:problem:revision-conflict', detail: 'the cadence changed since it was read' }),
      });

      await click('gardening-cadence-chore-edit-odd');
      await type('gardening-chore-edit-odd-note', 'typed');
      await click('gardening-chore-edit-odd-save');

      expect(stub.forRoute('/api/cadences/cad_evening', 'PATCH')).toHaveLength(1);
      expect(q('gardening-chore-edit-odd-line')?.textContent).toContain('the cadence changed since it was read');
      expect((q('gardening-chore-edit-odd-note') as HTMLInputElement).value).toBe('typed');

      await click('gardening-chore-edit-odd-cancel');
      expect(q('gardening-chore-edit-odd')).toBeNull();
      await click('gardening-cadence-chore-edit-odd');
      expect((q('gardening-chore-edit-odd-note') as HTMLInputElement).value).toBe('');
    });

    it('lands a hub refusal of the key on the key', async () => {
      const { click, type, q } = await render({
        patch: () => stubError(422, { detail: [{ loc: ['body', 'chores', 2, 'key'], msg: 'duplicate key', type: 'value_error' }] }),
      });

      await click('gardening-cadence-chore-add');
      await type('gardening-chore-add-key', 'odd');
      await type('gardening-chore-add-routine', 'deep');
      await click('gardening-chore-add-save');

      expect(q('gardening-chore-add-key')!.closest('fleet-kit-field')!.textContent).toContain('duplicate key');
      expect(q('gardening-chore-add')).not.toBeNull();
    });

    it('is open one at a time with the cadence edit and the remove confirm', async () => {
      const { click, q } = await render();

      await click('gardening-cadence-chore-edit-odd');
      await click('gardening-cadence-panel-edit');
      expect(q('gardening-chore-edit-odd')).toBeNull();
      expect(q('gardening-cadence-edit')).not.toBeNull();

      await click('gardening-cadence-chore-add');
      expect(q('gardening-cadence-edit')).toBeNull();
      await click('gardening-cadence-chore-remove-odd');
      expect(q('gardening-chore-add')).toBeNull();
      expect(q('confirm-dialog')).not.toBeNull();
    });

    it('answers a raced remove on the panel line, from the captured revision', async () => {
      const { click, q } = await render({ patch: () => stubError(409, { detail: 'the cadence changed since it was read' }) });

      await click('gardening-cadence-chore-remove-odd');
      await click('confirm-dialog-confirm');

      expect(q('gardening-cadence-panel-error')?.textContent).toContain('the cadence changed since it was read');
      expect(stub.forRoute('/api/cadences/cad_evening', 'PATCH')).toHaveLength(1);
    });
  });
});
