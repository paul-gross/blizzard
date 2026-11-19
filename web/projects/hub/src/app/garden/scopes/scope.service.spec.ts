import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { gardenScopeView, type RequestClientStub, stubError, stubHubClient } from 'fleet/testing';
import { HUB_PROJECT_FILTER } from '../../core/project-filter';
import { injectScopeService } from './scope.service';

describe('injectScopeService', () => {
  let stub: RequestClientStub;

  function configure(routes: Parameters<typeof stubHubClient>[0]) {
    stub = stubHubClient(routes);
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })),
        { provide: HUB_PROJECT_FILTER, useValue: signal('acme').asReadonly() },
      ],
    });
    return TestBed.runInInjectionContext(() => injectScopeService());
  }

  afterEach(() => stub.restore());

  it('edits the description from the draft, trimmed, and answers ok', async () => {
    const service = configure({ 'PATCH /api/projects/{project}/scopes/{slug}': () => gardenScopeView({ slug: 'web' }) });

    expect(await service.editDescription('web', { description: ' new ' })).toEqual({ ok: true });
    expect(stub.forRoute('/api/projects/acme/scopes/web', 'PATCH')[0].body).toEqual({ description: 'new' });
  });

  it('answers a field refusal on its field, never throwing', async () => {
    const service = configure({
      'PATCH /api/projects/{project}/scopes/{slug}': () =>
        stubError(422, { detail: [{ loc: ['body', 'description'], msg: 'too long', type: 'value_error' }] }),
    });

    expect(await service.editDescription('web', { description: 'x' })).toEqual({ ok: false, fields: { description: 'too long' }, form: null });
  });

  it('answers any other refusal on the form line', async () => {
    const service = configure({ 'PATCH /api/projects/{project}/scopes/{slug}': () => stubError(404, { detail: 'unknown scope web' }) });

    const outcome = await service.editDescription('web', { description: 'x' });
    expect(outcome).toMatchObject({ ok: false, fields: {} });
    expect(!outcome.ok && outcome.form).toContain('unknown scope');
  });
});
