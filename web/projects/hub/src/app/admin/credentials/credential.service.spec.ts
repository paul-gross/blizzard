import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { credentialSummary, type RequestClientStub, stubError, stubHubClient } from 'fleet/testing';
import { type Credential, type CredentialBinding } from './credential.domain';
import { injectCredentialService } from './credential.service';
import { toEditBaseline } from './credential.translate';

const bindings: readonly CredentialBinding[] = [{ name: 'token', forgeKind: 'github', settings: [{ key: 'ttl', type: { kind: 'positive-integer' } }] }];
const record: Credential = { id: 'cred_1', slug: 'slug', name: 'name', forgeId: 'forge', forgeSlug: 'f', forgeName: 'Forge', binding: 'token', secretName: 'secret', settings: { ttl: 5 }, authority: '', retired: false, revision: 3, createdBy: 'user', createdByName: null, createdAt: '' };

describe('credential service', () => {
  let stub: RequestClientStub;
  afterEach(() => { stub.restore(); TestBed.resetTestingModule(); });
  it('sends the captured revision and reports a conflict without retry', async () => {
    stub = stubHubClient({ 'PATCH /api/credentials/{credential}': () => stubError(409, { type: 'urn:conflict', detail: 'Stale revision.' }) });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient({ defaultOptions: { mutations: { retry: false } } }))] });
    const service = TestBed.runInInjectionContext(() => injectCredentialService());
    const baseline = toEditBaseline(record, bindings);
    expect(await service.edit(baseline, { ...baseline.draft, name: 'new' })).toEqual({ ok: false, fields: {}, form: 'Stale revision.' });
    const requests = stub.forRoute('/api/credentials/cred_1', 'PATCH');
    expect(requests).toHaveLength(1);
    expect(requests[0].headers['if-match']).toBe('3');
    expect(requests[0].body).toEqual({ name: 'new' });
  });
  it('maps fixed refusals to fields and retains setting paths at the form level', async () => {
    stub = stubHubClient({ 'POST /api/credentials': () => stubError(422, { detail: [
      { loc: ['body', 'slug'], msg: 'Already used', type: 'x' },
      { loc: ['body', 'settings', 'ttl'], msg: 'Out of range', type: 'x' },
    ] }) });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient({ defaultOptions: { mutations: { retry: false } } }))] });
    const service = TestBed.runInInjectionContext(() => injectCredentialService());
    const baseline = toEditBaseline(record, bindings);
    expect(await service.create(baseline.draft, bindings)).toEqual({ ok: false, fields: { slug: 'Already used' }, form: 'settings.ttl: Out of range' });
  });
  it('keeps the save pending until invalidation refetches finish', async () => {
    stub = stubHubClient({ 'PATCH /api/credentials/{credential}': () => credentialSummary() });
    const queryClient = new QueryClient();
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)] });
    const service = TestBed.runInInjectionContext(() => injectCredentialService());
    let release!: () => void;
    const refetched = new Promise<void>((resolve) => { release = resolve; });
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries').mockImplementation(() => refetched);
    const baseline = toEditBaseline(record, bindings);
    let done = false;
    const saved = service.edit(baseline, { ...baseline.draft, name: 'new' }).then(() => { done = true; });
    await vi.waitFor(() => expect(invalidate).toHaveBeenCalled());
    expect(done).toBe(false);
    release();
    await saved;
    expect(done).toBe(true);
  });
});
