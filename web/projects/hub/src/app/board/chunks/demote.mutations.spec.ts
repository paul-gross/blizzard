import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { hubClient } from 'fleet';
import { type RequestClientStub, stubRequestClient } from 'fleet/testing';
import { injectDemoteChunkMutation } from './demote.mutations';
import { injectPromoteChunkMutation } from './promote.mutations';

describe('the placement mutations', () => {
  let stub: RequestClientStub;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, () => ({}));
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    });
  });

  afterEach(() => stub.restore());

  it('demotes with the anchor in the body', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectDemoteChunkMutation());

    await mutation.mutateAsync({ chunkId: 'ch_a', afterChunkId: 'ch_b' });

    const posts = stub.forRoute('/api/chunks/ch_a/demote', 'POST');
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toEqual({ after_chunk_id: 'ch_b' });
  });

  it('demotes to the top of the backlog for a null anchor', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectDemoteChunkMutation());

    await mutation.mutateAsync({ chunkId: 'ch_a', afterChunkId: null });

    expect(stub.forRoute('/api/chunks/ch_a/demote', 'POST')[0].body).toEqual({ to_top: true });
  });

  it('sends no body for a plain promote, so the hub lands it at the tail', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectPromoteChunkMutation());

    await mutation.mutateAsync({ chunkId: 'ch_a' });

    expect(stub.forRoute('/api/chunks/ch_a/promote', 'POST')[0].body).toBeUndefined();
  });

  it('promotes with the anchor in the body', async () => {
    const mutation = TestBed.runInInjectionContext(() => injectPromoteChunkMutation());

    await mutation.mutateAsync({ chunkId: 'ch_a', afterChunkId: 'ch_c' });

    expect(stub.forRoute('/api/chunks/ch_a/promote', 'POST')[0].body).toEqual({ after_chunk_id: 'ch_c' });
  });
});
