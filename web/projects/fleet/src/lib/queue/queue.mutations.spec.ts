import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubError, stubRequestClient } from '../testing/stub-request-client';
import { injectRepositionBacklogMutation, injectRepositionQueueMutation } from './queue.mutations';

function deferred<T>(): { readonly promise: Promise<T>; readonly resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  return { promise: new Promise<T>((done) => (resolve = done)), resolve: (value) => resolve(value) };
}

describe('injectRepositionQueueMutation', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, () => ({ entries: [] }));
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  it('preserves submission order across hook instances when the first POST is slow', async () => {
    const firstPost = deferred<unknown>();
    const secondPost = deferred<unknown>();
    let postCount = 0;
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'POST' && path === '/api/queue/position') {
        return postCount++ === 0 ? firstPost.promise : secondPost.promise;
      }
      return {};
    });
    const refreshResolvers: (() => void)[] = [];
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries').mockImplementation(
      () => new Promise<void>((resolve) => refreshResolvers.push(resolve)),
    );
    const firstMutation = TestBed.runInInjectionContext(() => injectRepositionQueueMutation());
    const secondMutation = TestBed.runInInjectionContext(() => injectRepositionQueueMutation());

    const first = firstMutation.mutateAsync({ chunkId: 'ch_a', afterChunkId: 'ch_c' });
    await new Promise((resolve) => setTimeout(resolve, 0));
    let secondSettled = false;
    const second = secondMutation.mutateAsync({ chunkId: 'ch_b', afterChunkId: 'ch_a' }).then(() => {
      secondSettled = true;
    });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(stub.forRoute('/api/queue/position', 'POST')).toHaveLength(1);
    expect(invalidateSpy).not.toHaveBeenCalled();

    firstPost.resolve({ entries: [] });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(stub.forRoute('/api/queue/position', 'POST')).toHaveLength(1);
    expect(invalidateSpy).toHaveBeenCalledTimes(2);

    refreshResolvers.splice(0).forEach((resolve) => resolve());
    await first;
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(secondSettled).toBe(false);
    expect(invalidateSpy).toHaveBeenCalledTimes(2);
    expect(stub.forRoute('/api/queue/position', 'POST')).toHaveLength(2);

    secondPost.resolve({ entries: [] });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(invalidateSpy).toHaveBeenCalledTimes(4);
    refreshResolvers.splice(0).forEach((resolve) => resolve());
    await second;
    expect(secondSettled).toBe(true);
  });

  it('serializes backlog refreshes independently of the ready queue', async () => {
    const refreshResolvers: (() => void)[] = [];
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries').mockImplementation(
      () => new Promise<void>((resolve) => refreshResolvers.push(resolve)),
    );
    const firstMutation = TestBed.runInInjectionContext(() => injectRepositionBacklogMutation());
    const secondMutation = TestBed.runInInjectionContext(() => injectRepositionBacklogMutation());

    const first = firstMutation.mutateAsync({ chunkId: 'ch_a', afterChunkId: 'ch_c' });
    await new Promise((resolve) => setTimeout(resolve, 0));
    const second = secondMutation.mutateAsync({ chunkId: 'ch_b', afterChunkId: 'ch_a' });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(stub.forRoute('/api/backlog/position', 'POST')).toHaveLength(1);
    expect(invalidateSpy).toHaveBeenCalledTimes(2);
    expect(invalidateSpy.mock.calls.map((call) => call[0]?.queryKey)).toEqual([
      ['hub', 'backlog'],
      ['hub', 'chunks'],
    ]);

    refreshResolvers.splice(0).forEach((resolve) => resolve());
    await first;
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(stub.forRoute('/api/backlog/position', 'POST')).toHaveLength(2);
    expect(invalidateSpy).toHaveBeenCalledTimes(4);

    refreshResolvers.splice(0).forEach((resolve) => resolve());
    await second;
  });

  it('does not serialize the ready queue and backlog with each other', async () => {
    const refreshResolvers: (() => void)[] = [];
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries').mockImplementation(
      () => new Promise<void>((resolve) => refreshResolvers.push(resolve)),
    );
    const queueMutation = TestBed.runInInjectionContext(() => injectRepositionQueueMutation());
    const backlogMutation = TestBed.runInInjectionContext(() => injectRepositionBacklogMutation());

    const queue = queueMutation.mutateAsync({ chunkId: 'ch_queue', afterChunkId: null });
    await new Promise((resolve) => setTimeout(resolve, 0));
    const backlog = backlogMutation.mutateAsync({ chunkId: 'ch_backlog', afterChunkId: null });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(stub.forRoute('/api/queue/position', 'POST')).toHaveLength(1);
    expect(stub.forRoute('/api/backlog/position', 'POST')).toHaveLength(1);
    expect(invalidateSpy).toHaveBeenCalledTimes(4);

    refreshResolvers.splice(0).forEach((resolve) => resolve());
    await Promise.all([queue, backlog]);
  });

  it('runs the next reposition after an earlier POST is rejected and refreshed', async () => {
    const firstPost = deferred<ReturnType<typeof stubError>>();
    let postCount = 0;
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'POST' && path === '/api/queue/position' && postCount++ === 0) {
        return firstPost.promise;
      }
      return { entries: [] };
    });
    const refreshResolvers: (() => void)[] = [];
    vi.spyOn(queryClient, 'invalidateQueries').mockImplementation(
      () => new Promise<void>((resolve) => refreshResolvers.push(resolve)),
    );
    const errors: Error[] = [];
    const mutation = TestBed.runInInjectionContext(() => injectRepositionQueueMutation((error) => errors.push(error)));

    const first = mutation.mutateAsync({ chunkId: 'ch_a', afterChunkId: null }).catch((error: unknown) => error);
    await new Promise((resolve) => setTimeout(resolve, 0));
    const second = mutation.mutateAsync({ chunkId: 'ch_b', afterChunkId: null });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(stub.forRoute('/api/queue/position', 'POST')).toHaveLength(1);

    firstPost.resolve(stubError(409, { detail: 'stale anchor' }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(errors).toHaveLength(1);

    refreshResolvers.splice(0).forEach((resolve) => resolve());
    await first;
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(stub.forRoute('/api/queue/position', 'POST')).toHaveLength(2);

    refreshResolvers.splice(0).forEach((resolve) => resolve());
    await second;
  });
});
