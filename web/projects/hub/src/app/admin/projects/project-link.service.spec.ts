import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { sourceLinkView, stubHubClient } from 'fleet/testing';
import { projectLinkDraft } from './project-link.domain';
import { injectProjectLinkService } from './project-link.service';
import { projectLinkRefusal } from './project-link.translate';

describe('project link service', () => {
  it('initializes from eligible choices and rejects invalid direct calls', async () => {
    const choices = [{ value: 'a', label: 'A' }];
    expect(projectLinkDraft(choices)).toEqual({ name: 'a' });
    expect(projectLinkDraft([])).toEqual({ name: '' });
    const stub = stubHubClient({});
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())] });
    const service = TestBed.runInInjectionContext(injectProjectLinkService);
    expect((await service.link('p', 'source', { name: '' }, [])).ok).toBe(false);
    expect((await service.link('p', 'source', { name: 'other' }, choices)).ok).toBe(false);
    expect(stub.forRoute('/api/projects/p/source-links/other', 'PUT')).toHaveLength(0);
    stub.restore();
  });
  it('maps field and form refusals', () => {
    expect(projectLinkRefusal({ detail: [{ loc: ['body', 'name'], msg: 'not eligible' }] })).toEqual({ ok: false, fields: { name: 'not eligible' }, form: null });
    expect(projectLinkRefusal({ detail: 'unavailable' })).toEqual({ ok: false, fields: {}, form: 'unavailable' });
  });
  it('links without a revision and waits for refresh', async () => {
    const stub = stubHubClient({ 'PUT /api/projects/{project}/source-links/{source}': () => sourceLinkView() });
    const client = new QueryClient();
    let settle!: () => void;
    vi.spyOn(client, 'invalidateQueries').mockReturnValue(new Promise<void>((resolve) => { settle = resolve; }));
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(client)] });
    const service = TestBed.runInInjectionContext(injectProjectLinkService);
    const saving = service.link('p', 'source', { name: 'a' }, [{ value: 'a', label: 'A' }]);
    await vi.waitFor(() => expect(stub.forRoute('/api/projects/p/source-links/a', 'PUT')).toHaveLength(1));
    const call = stub.forRoute('/api/projects/p/source-links/a', 'PUT')[0];
    expect(call.headers['if-match']).toBeUndefined();
    let done = false;
    void saving.then(() => { done = true; });
    await Promise.resolve();
    expect(done).toBe(false);
    settle();
    expect(await saving).toEqual({ ok: true });
    stub.restore();
  });
});
