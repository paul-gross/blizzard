import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { runnerChunkDetailKey, runnerDashboardKey, runnerLeasesKey } from '../../core/query-keys';
import { injectChunkRequeueMutation } from './chunk-requeue.mutations';

@Component({ selector: 'app-test-requeue-host', template: '', changeDetection: ChangeDetectionStrategy.OnPush })
class RequeueHost {
  readonly mutation = injectChunkRequeueMutation(() => 'rn_1');
}

describe('injectChunkRequeueMutation', () => {
  let stub: RequestClientStub;
  beforeEach(() => {
    stub = stubRequestClient(runnerClient, () => ({ chunk_id: 'ch_1', requeued: true }));
  });
  afterEach(() => stub.restore());

  it('POSTs the requeue to the runner’s route and stays pending through its re-reads', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let resolveInvalidate!: () => void;
    const invalidated = vi.spyOn(client, 'invalidateQueries')
      .mockReturnValue(new Promise<void>((resolve) => (resolveInvalidate = resolve)));
    await TestBed.configureTestingModule({
      imports: [RequeueHost], providers: [provideZonelessChangeDetection(), provideTanStackQuery(client)],
    }).compileComponents();
    const fixture = TestBed.createComponent(RequeueHost);
    fixture.detectChanges();

    const requeue = fixture.componentInstance.mutation.mutateAsync({ runnerId: 'rn_1', chunkId: 'ch_1' });
    await new Promise((resolve) => setTimeout(resolve, 0));
    fixture.detectChanges();

    expect(stub.forRoute('/runners/rn_1/api/chunks/ch_1/requeues', 'POST')).toHaveLength(1);
    expect(invalidated).toHaveBeenCalledWith({ queryKey: runnerDashboardKey('rn_1') });
    expect(invalidated).toHaveBeenCalledWith({ queryKey: runnerLeasesKey('rn_1') });
    expect(invalidated).toHaveBeenCalledWith({ queryKey: runnerChunkDetailKey('rn_1', 'ch_1') });
    expect(fixture.componentInstance.mutation.isPending()).toBe(true);

    resolveInvalidate();
    await requeue;
    await new Promise((resolve) => setTimeout(resolve, 0));
    fixture.detectChanges();
    expect(fixture.componentInstance.mutation.isPending()).toBe(false);
  });
});
