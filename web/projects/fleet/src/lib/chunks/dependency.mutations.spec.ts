import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { injectDeclareDependencyMutation, injectReleaseDependencyMutation } from './dependency.mutations';

describe('injectDeclareDependencyMutation and injectReleaseDependencyMutation (issue #461)', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, () => ({}));
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  it('posts a declare with the prerequisite id and `by: operator`', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectDeclareDependencyMutation());

    await mutation.mutateAsync({ chunkId: 'ch_1', prerequisiteChunkId: 'ch_2' });

    const calls = stub.forRoute('/api/chunks/ch_1/dependencies', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ prerequisite_chunk_id: 'ch_2', by: 'operator' });
  });

  it('posts a release with the prerequisite id and `by: operator`', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectReleaseDependencyMutation());

    await mutation.mutateAsync({ chunkId: 'ch_1', prerequisiteChunkId: 'ch_2' });

    const calls = stub.forRoute('/api/chunks/ch_1/dependencies/release', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ prerequisite_chunk_id: 'ch_2', by: 'operator' });
  });

  it('invalidates both ranked lists and the chunk detail on a successful declare', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectDeclareDependencyMutation());

    await mutation.mutateAsync({ chunkId: 'ch_1', prerequisiteChunkId: 'ch_2' });

    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toContainEqual(['hub', 'chunks']);
    expect(keys).toContainEqual(['hub', 'queue']);
    expect(keys).toContainEqual(['hub', 'backlog']);
    expect(keys).toContainEqual(['hub', 'chunk', 'ch_1']);
  });

  it('invalidates both ranked lists and the chunk detail on a successful release', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectReleaseDependencyMutation());

    await mutation.mutateAsync({ chunkId: 'ch_1', prerequisiteChunkId: 'ch_2' });

    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toContainEqual(['hub', 'chunks']);
    expect(keys).toContainEqual(['hub', 'queue']);
    expect(keys).toContainEqual(['hub', 'backlog']);
    expect(keys).toContainEqual(['hub', 'chunk', 'ch_1']);
  });
});
