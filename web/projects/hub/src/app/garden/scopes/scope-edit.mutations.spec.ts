import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { HUB_PROJECT_FILTER } from 'fleet/shell';
import { gardenScopeView, type RequestClientStub, stubHubClient } from 'fleet/testing';
import { injectEditScopeMutation } from './scope-edit.mutations';

describe('injectEditScopeMutation', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubHubClient({
      'PATCH /api/projects/{project}/scopes/{slug}': () => gardenScopeView({ slug: 'blizzard', description: 'updated' }),
    });
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient), { provide: HUB_PROJECT_FILTER, useValue: signal('acme').asReadonly() }],
    });
  });

  afterEach(() => stub.restore());

  it('patches the description onto the scope', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectEditScopeMutation());

    await mutation.mutateAsync({ slug: 'blizzard', description: 'updated' });

    const calls = stub.forRoute('/api/projects/acme/scopes/blizzard', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ description: 'updated' });
  });

  it('invalidates the scope list on a successful edit', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectEditScopeMutation());

    await mutation.mutateAsync({ slug: 'blizzard', description: 'updated' });

    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toContainEqual(['hub', 'scopes']);
  });
});
