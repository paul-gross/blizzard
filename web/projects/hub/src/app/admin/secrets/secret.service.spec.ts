import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { type RequestClientStub, secretView, stubError, stubHubClient } from 'fleet/testing';

import { secret } from './secret.builders';
import { injectSecretService } from './secret.service';
import { toReplaceBaseline } from './secret.translate';

describe('injectSecretService', () => {
  let stub: RequestClientStub;

  function configure(routes: Parameters<typeof stubHubClient>[0]) {
    stub = stubHubClient(routes);
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })),
      ],
    });
    return TestBed.runInInjectionContext(() => injectSecretService());
  }

  afterEach(() => stub.restore());

  it('creates from a draft, sending no If-Match', async () => {
    const service = configure({ 'POST /api/secrets': () => secretView({ name: 'tok' }) });
    expect(await service.create({ name: ' tok ', value: 'hunter2' })).toEqual({ ok: true });
    const call = stub.forRoute('/api/secrets', 'POST')[0];
    expect(call.body).toEqual({ name: 'tok', value: 'hunter2' });
    expect(call.headers['if-match']).toBeUndefined();
  });

  it('answers a taken name on the name field', async () => {
    const service = configure({
      'POST /api/secrets': () => stubError(409, { type: 'urn:blizzard:problem:secret-name-taken', detail: 'name taken' }),
    });
    expect(await service.create({ name: 'tok', value: 'x' })).toEqual({ ok: false, fields: { name: 'name taken' }, form: null });
  });

  it('replaces against the baseline revision as If-Match', async () => {
    const service = configure({ 'PUT /api/secrets/{name}/value': () => secretView({ name: 'tok', revision: 4 }) });
    const baseline = toReplaceBaseline(secret({ name: 'tok', revision: 3 }));
    expect(await service.replace(baseline, { value: 'new' })).toEqual({ ok: true });
    const calls = stub.forRoute('/api/secrets/tok/value', 'PUT');
    expect(calls).toHaveLength(1);
    expect(calls[0].headers['if-match']).toBe('3');
    expect(calls[0].body).toEqual({ value: 'new' });
  });

  it('answers a revision conflict on the form line, sends once, and leaves the baseline and draft as they were', async () => {
    const service = configure({
      'PUT /api/secrets/{name}/value': () =>
        stubError(409, { type: 'urn:blizzard:problem:revision-conflict', detail: 'the secret changed since it was read' }),
    });
    const baseline = toReplaceBaseline(secret({ name: 'tok', revision: 3 }));
    const before = structuredClone(baseline);
    const draft = { value: 'typed' };

    expect(await service.replace(baseline, draft)).toEqual({ ok: false, fields: {}, form: 'the secret changed since it was read' });
    expect(stub.forRoute('/api/secrets/tok/value', 'PUT')).toHaveLength(1);
    expect(baseline).toEqual(before);
    expect(draft).toEqual({ value: 'typed' });
  });
});
