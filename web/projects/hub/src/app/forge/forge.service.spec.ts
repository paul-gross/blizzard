import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { forgeDetail, type RequestClientStub, stubError, stubHubClient } from 'fleet/testing';
import { hubConfigListKey } from '../core/query-keys';
import { forge } from './forge.builders';
import { EMPTY_FORGE_DRAFT } from './forge.domain';
import { injectForgeService } from './forge.service';
import { toForgeEditBaseline } from './forge.translate';

/** A refusal as the hub serves it; the declared problem bodies carry more than these specs read. */
const refuse = (status: number, body: object): never => stubError(status, body) as never;

describe('injectForgeService', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  function configure(routes: Parameters<typeof stubHubClient>[0]) {
    stub = stubHubClient(routes);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)] });
    return TestBed.runInInjectionContext(() => injectForgeService());
  }

  afterEach(() => stub.restore());

  it('sends the baseline revision as If-Match and a sparse body after a refresh moved the cached revision', async () => {
    const service = configure({ 'PATCH /api/forges/{forge}': () => forgeDetail({ revision: 6 }) });
    const baseline = toForgeEditBaseline(forge({ revision: 3 }));
    queryClient.setQueryData(hubConfigListKey('forges', false), [forgeDetail({ forge_id: 'frg_1', revision: 5 })]);

    const outcome = await service.edit(baseline, { ...baseline.draft, name: 'Enterprise' });

    expect(outcome).toEqual({ ok: true });
    const calls = stub.forRoute('/api/forges/frg_1', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].headers['if-match']).toBe('3');
    expect(calls[0].body).toEqual({ name: 'Enterprise' });
  });

  it('answers a revision conflict on the form line, sends once, and leaves the baseline and draft as they were', async () => {
    const service = configure({
      'PATCH /api/forges/{forge}': () =>
        refuse(409, { type: 'urn:blizzard:problem:forge-revision-conflict', detail: { code: 'revision_conflict', message: 'the forge changed since it was read' } }),
    });
    const baseline = toForgeEditBaseline(forge({ revision: 3 }));
    const before = structuredClone(baseline);
    const draft = { ...baseline.draft, webBase: 'https://w.test' };
    const typed = { ...draft };

    const outcome = await service.edit(baseline, draft);

    expect(outcome).toEqual({ ok: false, fields: {}, form: 'the forge changed since it was read' });
    expect(stub.forRoute('/api/forges/frg_1', 'PATCH')).toHaveLength(1);
    expect(baseline).toEqual(before);
    expect(draft).toEqual(typed);
  });

  it('answers a field refusal on its field, never throwing', async () => {
    const service = configure({
      'POST /api/forges': () => refuse(422, { detail: [{ loc: ['body', 'api_base'], msg: 'not a URL', type: 'value_error' }] }),
    });
    expect(await service.create({ ...EMPTY_FORGE_DRAFT, slug: 'g', kind: 'github', apiBase: 'x' })).toEqual({
      ok: false, fields: { apiBase: 'not a URL' }, form: null,
    });
  });

  it('answers a slug already taken on the slug', async () => {
    const service = configure({
      'POST /api/forges': () => refuse(409, { type: 'urn:blizzard:problem:forge-slug-taken', detail: 'slug taken' }),
    });
    expect(await service.create({ ...EMPTY_FORGE_DRAFT, slug: 'g', kind: 'github', apiBase: 'x' })).toEqual({
      ok: false, fields: { slug: 'slug taken' }, form: null,
    });
  });

  it('answers an origin already taken on the API base', async () => {
    const service = configure({
      'POST /api/forges': () => refuse(409, { type: 'urn:blizzard:problem:forge-origin-taken', detail: 'origin taken' }),
    });
    expect(await service.create({ ...EMPTY_FORGE_DRAFT, slug: 'g', kind: 'github', apiBase: 'x' })).toEqual({
      ok: false, fields: { apiBase: 'origin taken' }, form: null,
    });
  });

  it('answers a 5xx on the form line', async () => {
    const service = configure({ 'POST /api/forges': () => refuse(500, { detail: 'boom' }) });
    expect(await service.create({ ...EMPTY_FORGE_DRAFT, slug: 'g', kind: 'github', apiBase: 'x' })).toMatchObject({ ok: false, fields: {} });
  });

  it('creates the trimmed sparse draft and answers ok', async () => {
    const service = configure({ 'POST /api/forges': () => forgeDetail() });
    expect(await service.create({ ...EMPTY_FORGE_DRAFT, slug: ' ghe ', kind: 'github', apiBase: ' https://a.test ' })).toEqual({ ok: true });
    expect(stub.forRoute('/api/forges', 'POST')[0].body).toEqual({ slug: 'ghe', kind: 'github', api_base: 'https://a.test' });
  });

  describe.each([
    ['create', false],
    ['create', true],
    ['edit', false],
    ['edit', true],
  ] as const)('a %s with refused=%s', (op, refused) => {
    it('resolves only once the invalidations have refetched', async () => {
      const service = configure({
        'POST /api/forges': () => (refused ? refuse(422, { detail: 'no' }) : forgeDetail()),
        'PATCH /api/forges/{forge}': () => (refused ? refuse(422, { detail: 'no' }) : forgeDetail()),
      });
      let release!: () => void;
      const refetched = new Promise<void>((resolve) => (release = resolve));
      const spy = vi.spyOn(queryClient, 'invalidateQueries').mockImplementation(() => refetched);
      const baseline = toForgeEditBaseline(forge());

      let done = false;
      const saved = (
        op === 'create'
          ? service.create({ ...EMPTY_FORGE_DRAFT, slug: 'g', kind: 'github', apiBase: 'x' })
          : service.edit(baseline, { ...baseline.draft, name: 'n' })
      ).then(() => (done = true));
      await vi.waitFor(() => expect(spy).toHaveBeenCalled());
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(done).toBe(false);
      release();
      await saved;
      expect(done).toBe(true);
    });
  });
});
