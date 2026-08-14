import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { injectLocalPauseMutation } from './status.query';
import { runnerDashboardKey } from './query-keys';

/** A minimal host so the mutation — a `Component` field initializer concern —
 * runs inside a real injection context, and so `fixture.detectChanges()` can
 * flush the signal subscription `injectMutation` sets up via an `effect()`,
 * mirroring `leases.query.spec.ts`'s own host for `injectRunnerLeasesQuery`. */
@Component({
  selector: 'local-test-pause-mutation-host',
  template: '',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
class PauseMutationHost {
  readonly mutation = injectLocalPauseMutation();
}

describe('injectLocalPauseMutation (issue #133)', () => {
  let stub: RequestClientStub;

  beforeEach(() => {
    stub = stubRequestClient(runnerClient, (method, path) =>
      method === 'PATCH' && path === '/api/runner'
        ? { runner_id: 'runner-1', local_paused: true, hub_paused: false, paused: true }
        : {},
    );
  });

  afterEach(() => stub.restore());

  it('PATCHes /api/runner with the requested paused value', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await TestBed.configureTestingModule({
      imports: [PauseMutationHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    }).compileComponents();
    const fixture = TestBed.createComponent(PauseMutationHost);
    fixture.detectChanges();

    await fixture.componentInstance.mutation.mutateAsync(true);

    const calls = stub.forRoute('/api/runner', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ paused: true });
  });

  it('stays pending through the post-PATCH status re-read, not just the PATCH itself', async () => {
    // The stale-read window (issue #133 review): if `onSuccess` fired the
    // invalidation fire-and-forget, `isPending()` would clear the instant the
    // PATCH resolved — before `runnerDashboardKey` re-read lands — so the toggle
    // would re-enable while still showing the pre-flip label, and a fast
    // second click would compute its flip off the stale value. Holding
    // `isPending()` open until the invalidation itself settles is what
    // closes that window (`Mutation#execute` only dispatches `success`,
    // which is what flips `isPending()` to false, after `onSuccess` resolves).
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let resolveInvalidate!: () => void;
    const invalidateSpy = vi
      .spyOn(queryClient, 'invalidateQueries')
      .mockReturnValue(new Promise<void>((resolve) => (resolveInvalidate = resolve)));
    await TestBed.configureTestingModule({
      imports: [PauseMutationHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    }).compileComponents();
    const fixture = TestBed.createComponent(PauseMutationHost);
    fixture.detectChanges();

    const flip = fixture.componentInstance.mutation.mutateAsync(true);
    // Let the stubbed PATCH's own promise chain (fetch → generated client → mutationFn)
    // fully resolve, then flush the signal subscription, before asserting — a macrotask
    // tick is enough since the stub never does real I/O.
    await new Promise((resolve) => setTimeout(resolve, 0));
    fixture.detectChanges();

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: runnerDashboardKey });
    expect(fixture.componentInstance.mutation.isPending()).toBe(true);

    resolveInvalidate();
    await flip;
    await new Promise((resolve) => setTimeout(resolve, 0));
    fixture.detectChanges();

    expect(fixture.componentInstance.mutation.isPending()).toBe(false);
  });
});
