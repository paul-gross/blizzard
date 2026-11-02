import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { runnerDashboardKey } from '../core/query-keys';
import { injectSelftestStartMutation } from './selftest.mutations';

@Component({ selector: 'app-test-selftest-host', template: '', changeDetection: ChangeDetectionStrategy.OnPush })
class SelftestHost {
  readonly mutation = injectSelftestStartMutation(() => 'rn_1');
}

describe('injectSelftestStartMutation', () => {
  let stub: RequestClientStub;
  beforeEach(() => {
    stub = stubRequestClient(runnerClient, () => ({ id: 'st_1', harness: 'mock', status: 'running', checks: [], error: null }));
  });
  afterEach(() => stub.restore());

  it('POSTs the harness and stays pending through the dashboard re-read', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let resolveInvalidate!: () => void;
    const invalidated = vi.spyOn(client, 'invalidateQueries')
      .mockReturnValue(new Promise<void>((resolve) => (resolveInvalidate = resolve)));
    await TestBed.configureTestingModule({
      imports: [SelftestHost], providers: [provideZonelessChangeDetection(), provideTanStackQuery(client)],
    }).compileComponents();
    const fixture = TestBed.createComponent(SelftestHost);
    fixture.detectChanges();

    const start = fixture.componentInstance.mutation.mutateAsync({ runnerId: 'rn_1', harness: 'mock' });
    await new Promise((resolve) => setTimeout(resolve, 0));
    fixture.detectChanges();

    const calls = stub.forRoute('/runners/rn_1/api/selftests', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ harness: 'mock' });
    expect(invalidated).toHaveBeenCalledWith({ queryKey: runnerDashboardKey('rn_1') });
    expect(fixture.componentInstance.mutation.isPending()).toBe(true);

    resolveInvalidate();
    expect((await start).id).toBe('st_1');
  });
});
