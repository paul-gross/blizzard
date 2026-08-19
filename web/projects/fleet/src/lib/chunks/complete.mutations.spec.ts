import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { injectCompleteChunkMutation } from './complete.mutations';

describe('injectCompleteChunkMutation (issue #294)', () => {
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

  it('posts the complete verb with `by: operator`', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectCompleteChunkMutation());

    await mutation.mutateAsync({ chunkId: 'ch_1' });

    const calls = stub.forRoute('/api/chunks/ch_1/complete', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ by: 'operator' });
  });

  it('invalidates the chunks list, the ready queue, and the chunk detail on success', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectCompleteChunkMutation());

    await mutation.mutateAsync({ chunkId: 'ch_1' });

    const keys = invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
    expect(keys).toContainEqual(['hub', 'chunks']);
    expect(keys).toContainEqual(['hub', 'queue']);
    expect(keys).toContainEqual(['hub', 'chunk', 'ch_1']);
  });
});
