import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { injectGroupChunksMutation, injectRepositionQueueMutation } from './queue.mutations';

describe('injectRepositionQueueMutation', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (path === '/api/queue/position') return { entries: [] };
      return {};
    });
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  it('sends the moved chunk and its anchor', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectRepositionQueueMutation());

    await mutation.mutateAsync({ chunkId: 'ch_1', afterChunkId: 'ch_2' });

    const calls = stub.forRoute('/api/queue/position', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ chunk_id: 'ch_1', after_chunk_id: 'ch_2' });
  });

  it('invalidates the queue and the chunks list on success', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectRepositionQueueMutation());

    await mutation.mutateAsync({ chunkId: 'ch_1', afterChunkId: null });

    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toContainEqual(['hub', 'queue']);
    expect(keys).toContainEqual(['hub', 'chunks']);
  });
});

describe('injectGroupChunksMutation (issue #214)', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (path === '/api/chunks/ch_survivor/group') {
        return { chunk_id: 'ch_survivor', work_refs: [], merged_chunk_ids: ['ch_merged'] };
      }
      return {};
    });
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  it('merges the selected chunks into the survivor', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectGroupChunksMutation());

    await mutation.mutateAsync({ survivorId: 'ch_survivor', mergeChunkIds: ['ch_merged'] });

    const calls = stub.forRoute('/api/chunks/ch_survivor/group', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ merge_chunk_ids: ['ch_merged'] });
  });

  it('invalidates the ready queue and the chunks list on success, so a grouped board updates without a reload', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectGroupChunksMutation());

    await mutation.mutateAsync({ survivorId: 'ch_survivor', mergeChunkIds: ['ch_merged'] });

    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toContainEqual(['hub', 'queue']);
    expect(keys).toContainEqual(['hub', 'chunks']);
  });
});
