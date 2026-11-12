import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideTanStackQuery, QueryClient } from '@tanstack/angular-query-experimental';
import { gardenRoutineView, stubHubClient } from 'fleet/testing';
import { injectCreateRoutineMutation } from './routine-create.mutations';
import { HUB_PROJECT_FILTER } from 'fleet/shell';

describe('create routine', () => {
  it('sends the selected urgency or null for the tenant default', async () => {
    const stub = stubHubClient({ 'POST /api/projects/{project}/routines': () => gardenRoutineView({ routine_id: 'r1', name: 'nightly' }) });
    try {
      TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient()), { provide: HUB_PROJECT_FILTER, useValue: signal('blizzard').asReadonly() }] });
      const mutation = TestBed.runInInjectionContext(() => injectCreateRoutineMutation());
      const body = { slug: 'nightly', graph_name: 'garden', default_scope_slug: 'web', default_urgency: null };
      await mutation.mutateAsync(body);
      await mutation.mutateAsync({ ...body, default_urgency: 'urgent' });
      const calls = stub.forRoute('/api/projects/blizzard/routines', 'POST');
      expect(calls[0].body).toEqual(body);
      expect(calls[1].body).toEqual({ ...body, default_urgency: 'urgent' });
    } finally { stub.restore(); }
  });
});
