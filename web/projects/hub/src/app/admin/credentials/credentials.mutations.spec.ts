import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';
import { hubConfigKey } from 'fleet';
import { credentialSummary, type RequestClientStub, stubHubClient } from 'fleet/testing';
import { injectCreateCredentialMutation, injectCredentialLifecycleMutation, injectEditCredentialMutation } from './credentials.mutations';

describe('credential mutations', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubHubClient({
      'POST /api/credentials': () => credentialSummary(),
      'PATCH /api/credentials/{credential}': () => credentialSummary({ revision: 3 }),
      'POST /api/credentials/{credential}/retire': () => credentialSummary({ retired: true, revision: 4 }),
      'POST /api/credentials/{credential}/enable': () => credentialSummary({ revision: 5 }),
    });
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)] });
  });
  afterEach(() => stub.restore());

  it('creates a credential and invalidates all config reads', async () => {
    const spy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectCreateCredentialMutation());
    const body = { slug: 'gh-main', forge: 'frg_g', binding: 'github.token', secret: 'gh-token' };
    await mutation.mutateAsync(body);
    expect(stub.forRoute('/api/credentials', 'POST')[0].body).toEqual(body);
    expect(spy.mock.calls.map(([args]) => args)).toContainEqual({ queryKey: hubConfigKey });
  });

  it('patches with If-Match and routes lifecycle by desired state', async () => {
    const edit = TestBed.runInInjectionContext(() => injectEditCredentialMutation());
    const lifecycle = TestBed.runInInjectionContext(() => injectCredentialLifecycleMutation());
    await edit.mutateAsync({ credentialId: 'cred_g', revision: 2, body: { settings: { variant: 'classic' } } });
    await lifecycle.mutateAsync({ credentialId: 'cred_g', revision: 3, retired: true });
    await lifecycle.mutateAsync({ credentialId: 'cred_g', revision: 4, retired: false });
    expect(stub.forRoute('/api/credentials/cred_g', 'PATCH')[0]).toMatchObject({ body: { settings: { variant: 'classic' } }, headers: { 'if-match': '2' } });
    expect(stub.forRoute('/api/credentials/cred_g/retire', 'POST')[0].headers['if-match']).toBe('3');
    expect(stub.forRoute('/api/credentials/cred_g/enable', 'POST')[0].headers['if-match']).toBe('4');
  });
});
