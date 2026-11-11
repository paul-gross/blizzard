import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';
import { stubHubClient } from 'fleet/testing';
import { injectChunkUrgencyMutation } from './urgency.mutations';

describe('chunk urgency mutation', () => {
  it('patches the chosen urgency and refreshes detail and both ranked lists', async () => {
    const stub = stubHubClient({ 'PATCH /api/chunks/{chunk_id}': (_request, { chunk_id }) => ({ chunk_id, default_effort: null, default_harnesses: [], default_model: [], graph_id: 'gr_1', intended_migration: null, project_id: null, project_name: null, project_slug: null, urgency: { urgency_id: 'u', slug: 'urgent' } }) });
    const client = new QueryClient();
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(client)] });
    const invalidated = vi.spyOn(client, 'invalidateQueries');
    const mutation = TestBed.runInInjectionContext(() => injectChunkUrgencyMutation());
    try {
      await mutation.mutateAsync({ chunkId: 'ch_1', urgencyId: 'u' });
      expect(stub.forRoute('/api/chunks/ch_1', 'PATCH')[0].body).toEqual({ urgency: 'u' });
      const keys = invalidated.mock.calls.map(([arg]) => arg?.queryKey);
      expect(keys).toContainEqual(['hub', 'chunks']);
      expect(keys).toContainEqual(['hub', 'chunk', 'ch_1']);
      expect(keys).toContainEqual(['hub', 'queue']);
      expect(keys).toContainEqual(['hub', 'backlog']);
    } finally { stub.restore(); client.clear(); }
  });
});
