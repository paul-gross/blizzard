import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';
import { hubClient, hubConfigKey } from 'fleet';
import { type RequestClientStub, stubRequestClient } from 'fleet/testing';
import { injectCreateForgeMutation, injectEditForgeMutation, injectForgeLifecycleMutation } from './forges.mutations';

describe('forge mutations', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, () => ({}));
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)] });
  });
  afterEach(() => stub.restore());

  it('creates a forge and invalidates all config reads', async () => {
    const spy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectCreateForgeMutation());
    await mutation.mutateAsync({ slug: 'github', kind: 'github', api_base: 'https://api.github.com' });
    expect(stub.forRoute('/api/forges', 'POST')[0].body).toEqual({ slug: 'github', kind: 'github', api_base: 'https://api.github.com' });
    expect(spy.mock.calls.map(([args]) => args)).toContainEqual({ queryKey: hubConfigKey });
  });

  it('patches only mutable fields with If-Match and routes lifecycle by desired state', async () => {
    const edit = TestBed.runInInjectionContext(() => injectEditForgeMutation());
    const lifecycle = TestBed.runInInjectionContext(() => injectForgeLifecycleMutation());
    await edit.mutateAsync({ forgeId: 'frg_a', revision: 2, body: { name: 'Enterprise' } });
    await lifecycle.mutateAsync({ forgeId: 'frg_a', revision: 3, retired: true });
    await lifecycle.mutateAsync({ forgeId: 'frg_a', revision: 4, retired: false });
    expect(stub.forRoute('/api/forges/frg_a', 'PATCH')[0]).toMatchObject({ body: { name: 'Enterprise' }, headers: { 'if-match': '2' } });
    expect(stub.forRoute('/api/forges/frg_a/retire', 'POST')[0].headers['if-match']).toBe('3');
    expect(stub.forRoute('/api/forges/frg_a/enable', 'POST')[0].headers['if-match']).toBe('4');
  });
});
