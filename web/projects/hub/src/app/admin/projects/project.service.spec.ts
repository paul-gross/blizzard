import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { projectView, stubHubClient } from 'fleet/testing';
import { injectProjectService } from './project.service';

describe('project service', () => {
  it('rejects invalid direct calls without a mutation', async () => {
    const stub = stubHubClient({});
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())] });
    const service = TestBed.runInInjectionContext(injectProjectService);
    const draft = { slug: 'BAD', name: '', description: '' };
    expect((await service.create(draft, vi.fn())).ok).toBe(false);
    expect((await service.edit({ id: 'p', revision: 1, draft }, draft, vi.fn())).ok).toBe(false);
    expect(stub.forRoute('/api/projects', 'POST')).toHaveLength(0);
    expect(stub.forRoute('/api/projects/p', 'PATCH')).toHaveLength(0);
    stub.restore();
  });
  it('uses the captured baseline and notifies only after invalidation settles', async () => {
    const stub = stubHubClient({ 'PATCH /api/projects/{project}': () => projectView() });
    const client = new QueryClient();
    let settle!: () => void;
    const invalidation = new Promise<void>((resolve) => { settle = resolve; });
    vi.spyOn(client, 'invalidateQueries').mockReturnValue(invalidation);
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(client)] });
    const service = TestBed.runInInjectionContext(injectProjectService);
    const draft = { slug: 'alpha', name: 'Alpha', description: 'old' };
    const success = vi.fn();
    const saving = service.edit({ id: 'p', revision: 4, draft }, { ...draft, description: '' }, success);
    await vi.waitFor(() => expect(stub.forRoute('/api/projects/p', 'PATCH')).toHaveLength(1));
    const call = stub.forRoute('/api/projects/p', 'PATCH')[0];
    expect(call.body).toEqual({ description: '' });
    expect(call.headers['if-match']).toBe('4');
    expect(success).not.toHaveBeenCalled();
    settle();
    expect(await saving).toEqual({ ok: true });
    expect(success).toHaveBeenCalledOnce();
    stub.restore();
  });
});
