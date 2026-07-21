import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { injectSetChunkGraphMutation, injectSetChunkModelMutation } from './edit.mutations';

describe('injectSetChunkGraphMutation / injectSetChunkModelMutation (issue #27)', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (path === '/api/chunks/ch_1') return { chunk_id: 'ch_1', graph_id: 'gr_alt', model: 'claude-sonnet-4-5' };
      return {};
    });
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  it('patches the target graph id onto the chunk', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectSetChunkGraphMutation());

    await mutation.mutateAsync({ chunkId: 'ch_1', graphId: 'gr_alt' });

    const calls = stub.forRoute('/api/chunks/ch_1', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ graph_id: 'gr_alt' });
  });

  it('invalidates the chunks list and the chunk detail on a successful graph edit', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectSetChunkGraphMutation());

    await mutation.mutateAsync({ chunkId: 'ch_1', graphId: 'gr_alt' });

    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toContainEqual(['hub', 'chunks']);
    expect(keys).toContainEqual(['hub', 'chunk', 'ch_1']);
  });

  it('patches the target model onto the chunk', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectSetChunkModelMutation());

    await mutation.mutateAsync({ chunkId: 'ch_1', model: 'claude-sonnet-4-5' });

    const calls = stub.forRoute('/api/chunks/ch_1', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ model: 'claude-sonnet-4-5' });
  });

  it('invalidates the chunks list and the chunk detail on a successful model edit', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectSetChunkModelMutation());

    await mutation.mutateAsync({ chunkId: 'ch_1', model: 'claude-sonnet-4-5' });

    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toContainEqual(['hub', 'chunks']);
    expect(keys).toContainEqual(['hub', 'chunk', 'ch_1']);
  });
});
