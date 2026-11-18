import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { type RequestClientStub, stubError, stubHubClient, workSourceSummary } from 'fleet/testing';

import { workSource } from './work-source.builders';
import { injectWorkSourceService } from './work-source.service';
import { toEditBaseline } from './work-source.translate';

describe('injectWorkSourceService', () => {
  let stub: RequestClientStub;

  function configure(routes: Parameters<typeof stubHubClient>[0]) {
    stub = stubHubClient(routes);
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })),
      ],
    });
    return TestBed.runInInjectionContext(() => injectWorkSourceService());
  }

  afterEach(() => stub.restore());

  const DRAFT = { slug: 'winter', name: '', provider: 'github', locator: 'a/b', forge: 'frg_github', credential: '', annotate: false };

  it('creates from a draft', async () => {
    const service = configure({ 'POST /api/work-sources': () => workSourceSummary({ slug: 'winter' }) });
    expect(await service.create(DRAFT)).toEqual({ ok: true });
    expect(stub.forRoute('/api/work-sources', 'POST')[0].body).toEqual({
      slug: 'winter',
      provider: 'github',
      locator: 'a/b',
      forge: 'frg_github',
      annotate: false,
    });
  });

  it('answers a taken slug on the slug field, with the create fallback for the unnamed', async () => {
    const service = configure({
      'POST /api/work-sources': () => stubError(409, { type: 'urn:blizzard:problem:work-source-slug-taken', detail: 'slug taken' }),
    });
    expect(await service.create(DRAFT)).toEqual({ ok: false, fields: { slug: 'slug taken' }, form: null });
  });

  it('answers a field validation refusal on its field', async () => {
    const service = configure({
      'POST /api/work-sources': () => stubError(422, { detail: [{ loc: ['body', 'forge'], msg: 'forge is required', type: 'x' }] }),
    });
    expect(await service.create(DRAFT)).toEqual({ ok: false, fields: { forge: 'forge is required' }, form: null });
  });

  it('edits against the baseline revision, sending only what changed', async () => {
    const service = configure({ 'PATCH /api/work-sources/{source}': () => workSourceSummary({ revision: 4 }) });
    const baseline = toEditBaseline(workSource({ id: 'wsrc_j', revision: 3 }))!;
    expect(await service.edit(baseline, { ...baseline.draft, name: 'Acme' })).toEqual({ ok: true });
    const calls = stub.forRoute('/api/work-sources/wsrc_j', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].headers['if-match']).toBe('3');
    expect(calls[0].body).toEqual({ name: 'Acme' });
  });

  it('answers a revision conflict on the form line, sends once, and leaves the baseline and draft as they were', async () => {
    const service = configure({
      'PATCH /api/work-sources/{source}': () =>
        stubError(409, { type: 'urn:blizzard:problem:revision-conflict', detail: 'the work source changed since it was read' }),
    });
    const baseline = toEditBaseline(workSource({ id: 'wsrc_j', revision: 3 }))!;
    const before = structuredClone(baseline);
    const draft = { ...baseline.draft, name: 'typed' };
    const typed = { ...draft };

    expect(await service.edit(baseline, draft)).toEqual({ ok: false, fields: {}, form: 'the work source changed since it was read' });
    expect(stub.forRoute('/api/work-sources/wsrc_j', 'PATCH')).toHaveLength(1);
    expect(baseline).toEqual(before);
    expect(draft).toEqual(typed);
  });
});
