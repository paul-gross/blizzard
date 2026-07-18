import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { injectChunkPauseMutation } from './pause.mutations';

describe('injectChunkPauseMutation (issue #46)', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (path === '/api/chunks/ch_1/pause') return {};
      if (path === '/api/chunks/ch_1/resume') return {};
      return {};
    });
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  it('routes to the pause verb when paused=true, with `by: operator`', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectChunkPauseMutation());

    await mutation.mutateAsync({ chunkId: 'ch_1', paused: true });

    const calls = stub.forRoute('/api/chunks/ch_1/pause', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ by: 'operator' });
    expect(stub.forRoute('/api/chunks/ch_1/resume', 'POST')).toHaveLength(0);
  });

  it('routes to the resume verb when paused=false', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectChunkPauseMutation());

    await mutation.mutateAsync({ chunkId: 'ch_1', paused: false });

    const calls = stub.forRoute('/api/chunks/ch_1/resume', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ by: 'operator' });
    expect(stub.forRoute('/api/chunks/ch_1/pause', 'POST')).toHaveLength(0);
  });

  it('invalidates the chunks list, the ready queue, and the chunk detail on success', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectChunkPauseMutation());

    await mutation.mutateAsync({ chunkId: 'ch_1', paused: true });

    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toContainEqual(['hub', 'chunks']);
    expect(keys).toContainEqual(['hub', 'queue']);
    expect(keys).toContainEqual(['hub', 'chunk', 'ch_1']);
  });
});
