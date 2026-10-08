import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { formatWhen, hubClient, hubDecisionsKey } from 'fleet';
import { type RequestClientStub, stubError, stubRequestClient } from 'fleet/testing';
import { injectResolveDecisionMutation, readDecisionFailure } from './human.mutations';

describe('injectResolveDecisionMutation', () => {
  let stub: RequestClientStub;
  let queryClient: QueryClient;
  let resolveResponse: unknown;

  beforeEach(() => {
    resolveResponse = { decision_id: 'dc_1', choice: 'approve', resolved_by: 'operator', resolved_at: '2026-07-16T00:00:00Z' };
    stub = stubRequestClient(hubClient, (method, path) =>
      method === 'POST' && path === '/api/decisions/dc_1/resolutions' ? resolveResponse : {},
    );
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient)],
    });
  });

  afterEach(() => stub.restore());

  const keysAfter = async (): Promise<readonly unknown[]> => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectResolveDecisionMutation());
    await mutation.mutateAsync({ decisionId: 'dc_1', choice: 'approve', chunkId: 'ch_1' }).catch(() => undefined);
    return invalidateSpy.mock.calls.map((call) => (call[0] as { queryKey: readonly unknown[] }).queryKey);
  };

  it('re-reads the chunk, the fleet list, and the fleet-wide open gates once settled', async () => {
    const keys = await keysAfter();

    expect(keys).toContainEqual(['hub', 'chunk', 'ch_1']);
    expect(keys).toContainEqual(['hub', 'chunks']);
    expect(keys).toContainEqual(hubDecisionsKey);
  });

  it('re-reads the same keys after a lost race — someone else’s resolution landed', async () => {
    resolveResponse = stubError(409, {
      decision_id: 'dc_1',
      already_resolved_by: 'alice',
      resolved_choice: 'reject',
      resolved_at: '2026-07-16T00:00:00Z',
    });

    const keys = await keysAfter();

    expect(keys).toContainEqual(['hub', 'chunk', 'ch_1']);
    expect(keys).toContainEqual(hubDecisionsKey);
  });
});

describe('readDecisionFailure', () => {
  const NOW = new Date(2026, 6, 16, 12, 0);

  it('reads a lost race as the winning choice, who, and when', () => {
    const failure = readDecisionFailure({
      decision_id: 'dc_1',
      already_resolved_by: 'alice',
      resolved_choice: 'reject',
      resolved_at: '2026-07-16T00:00:00Z',
      detail: 'decision already resolved',
    }, NOW);

    expect(failure).toEqual({ kind: 'outcome', message: `reject by alice, ${formatWhen('2026-07-16T00:00:00Z', NOW)}` });
  });

  it('keeps any other failure on the error channel', () => {
    expect(readDecisionFailure({ detail: 'unknown decision dc_1' }, NOW)).toEqual({ kind: 'error', message: 'unknown decision dc_1' });
    expect(readDecisionFailure(undefined, NOW).kind).toBe('error');
  });
});
