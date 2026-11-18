import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { gardenCadenceView, gardenChoreView, type RequestClientStub, stubError, stubHubClient } from 'fleet/testing';
import { HUB_PROJECT_FILTER } from '../../core/project-filter';
import { gardenCadence, gardenChore } from '../core/cadence.builders';
import { EMPTY_CHORE_DRAFT } from '../core/cadence.domain';
import { toChoreDraft, toChoresBaseline } from '../core/cadence.translate';
import { injectChoreService } from './chore.service';

const ODD = gardenChore({ id: 'chr_2', key: 'odd', routine: 'docs', scopes: ['web'], mode: { unlisted: 'sampled' }, urgency: 'someday' });
const CADENCE = gardenCadence({ slug: 'evening', revision: 3, chores: [gardenChore(), ODD] });
const NIGHTLY_DOC = { key: 'nightly', routine: 'nightly', scopes: 'all', mode: 'full', promote: true, note: null, urgency: null };
const ODD_DOC = { key: 'odd', routine: 'docs', scopes: ['web'], mode: 'sampled', promote: true, note: null, urgency: 'someday' };

describe('injectChoreService', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  function configure(routes: Parameters<typeof stubHubClient>[0]) {
    stub = stubHubClient(routes);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(queryClient),
        { provide: HUB_PROJECT_FILTER, useValue: signal('acme').asReadonly() },
      ],
    });
    return TestBed.runInInjectionContext(() => injectChoreService());
  }
  const patches = () => stub.forRoute(`/api/cadences/${CADENCE.id}`, 'PATCH');
  const ok = { 'PATCH /api/cadences/{cadence_id}': () => gardenCadenceView({ slug: 'evening', chores: [gardenChoreView()] }) };

  afterEach(() => stub.restore());

  it('adds at the end, resending the held chores as read, against the captured revision', async () => {
    const service = configure(ok);
    const before = toChoresBaseline(CADENCE);

    const outcome = await service.add(before, { ...EMPTY_CHORE_DRAFT, key: 'weekly', routine: 'deep', scopes: 'a, b' });

    expect(outcome).toEqual({ ok: true });
    expect(patches()).toHaveLength(1);
    expect(patches()[0].headers['if-match']).toBe('3');
    expect(patches()[0].body).toEqual({
      chores: [
        NIGHTLY_DOC,
        ODD_DOC,
        { key: 'weekly', routine: 'deep', scopes: ['a', 'b'], mode: 'full', promote: true, note: null, urgency: null },
      ],
    });
  });

  it('edits in place, keeping the target key and every other chore, an unlisted mode included', async () => {
    const service = configure(ok);
    const before = toChoresBaseline(CADENCE);

    await service.edit(before, 'nightly', { ...toChoreDraft(CADENCE.chores[0]), key: 'other', promote: false });

    expect(patches()[0].body).toEqual({ chores: [{ ...NIGHTLY_DOC, promote: false }, ODD_DOC] });
  });

  it('removes one chore', async () => {
    const service = configure(ok);
    await service.remove(toChoresBaseline(CADENCE), 'nightly');
    expect(patches()[0].body).toEqual({ chores: [ODD_DOC] });
  });

  it('answers a revision conflict on the form line from one request, with nothing rebuilt', async () => {
    const service = configure({
      'PATCH /api/cadences/{cadence_id}': () =>
        stubError(409, { type: 'urn:blizzard:problem:revision-conflict', detail: 'the cadence changed since it was read' }),
    });
    const before = toChoresBaseline(CADENCE);
    const draft = { ...toChoreDraft(CADENCE.chores[1]), routine: 'other' };
    const held = structuredClone(before);

    const outcome = await service.edit(before, 'odd', draft);

    expect(outcome).toEqual({ ok: false, fields: {}, form: 'the cadence changed since it was read' });
    expect(patches()).toHaveLength(1);
    expect(before).toEqual(held);
  });

  it("answers a field refusal on the target chore's field, and another chore's on the form line", async () => {
    const detail = (index: number) => ({ detail: [{ loc: ['body', 'chores', index, 'routine'], msg: 'unknown routine', type: 'value_error' }] });
    let index = 1;
    const service = configure({ 'PATCH /api/cadences/{cadence_id}': () => stubError(422, detail(index)) });
    const before = toChoresBaseline(CADENCE);
    const draft = toChoreDraft(CADENCE.chores[1]);

    expect(await service.edit(before, 'odd', draft)).toEqual({ ok: false, fields: { routine: 'unknown routine' }, form: null });
    index = 0;
    expect(await service.edit(before, 'odd', draft)).toEqual({ ok: false, fields: {}, form: 'chores[0].routine: unknown routine' });
  });

  it('answers an add refusal on the new chore, at the end of the list', async () => {
    const service = configure({
      'PATCH /api/cadences/{cadence_id}': () =>
        stubError(422, { detail: [{ loc: ['body', 'chores', 2, 'key'], msg: 'duplicate key', type: 'value_error' }] }),
    });
    expect(await service.add(toChoresBaseline(CADENCE), { ...EMPTY_CHORE_DRAFT, key: 'odd', routine: 'x' })).toEqual({
      ok: false,
      fields: { key: 'duplicate key' },
      form: null,
    });
  });

  it('resolves a save only once the invalidations have refetched', async () => {
    const service = configure(ok);
    let release!: () => void;
    const refetched = new Promise<void>((resolve) => (release = resolve));
    const spy = vi.spyOn(queryClient, 'invalidateQueries').mockImplementation(() => refetched);

    let done = false;
    const saved = service.remove(toChoresBaseline(CADENCE), 'odd').then(() => (done = true));
    await vi.waitFor(() => expect(spy).toHaveBeenCalledTimes(3));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(done).toBe(false);
    release();
    await saved;
    expect(done).toBe(true);
  });
});
