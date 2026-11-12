import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubConfigKey, type UrgencyView } from 'fleet';
import { type RequestClientStub, stubHubClient } from 'fleet/testing';
import { vi } from 'vitest';
import { injectCreateUrgencyMutation, injectDefaultUrgencyMutation, injectEditUrgencyMutation, injectPositionUrgencyMutation, injectUrgencyLifecycleMutation } from './urgencies.mutations';

const urgency: UrgencyView = { urgency_id: 'u', slug: 'soon', name: 'Soon', color: 'red', emoji: null, icon: null, retired: false, default: false, revision: 1, open_chunks: 0, place: 0, created_at: '2026-01-01T00:00:00Z', created_by: 'u' };

describe('urgency mutations', () => {
  let stub: RequestClientStub;
  let client: QueryClient;
  beforeEach(() => {
    stub = stubHubClient({
      'POST /api/urgencies': () => urgency,
      'PATCH /api/urgencies/{key}': () => urgency,
      'POST /api/urgencies/{key}/retire': () => ({ ...urgency, moved_chunks: 1 }),
      'POST /api/urgencies/{key}/enable': () => urgency,
      'POST /api/urgencies/order/position': () => ({ urgencies: [urgency] }),
      'PATCH /api/settings': () => ({ default_urgency: { urgency_id: 'u', slug: 'soon' }, revision: 8, updated_at: '2026-01-01T00:00:00Z', updated_by: 'u' }),
    });
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(client)] });
  });
  afterEach(() => stub.restore());
  it('sends revision preconditions and single-position moves and invalidates config', async () => {
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const create = TestBed.runInInjectionContext(injectCreateUrgencyMutation);
    const edit = TestBed.runInInjectionContext(injectEditUrgencyMutation);
    const lifecycle = TestBed.runInInjectionContext(injectUrgencyLifecycleMutation);
    const position = TestBed.runInInjectionContext(injectPositionUrgencyMutation);
    const setDefault = TestBed.runInInjectionContext(injectDefaultUrgencyMutation);
    await create.mutateAsync({ slug: 'soon' });
    await edit.mutateAsync({ key: 'u', revision: 2, body: { name: 'Soon' } });
    await lifecycle.mutateAsync({ key: 'u', revision: 3, retired: true, moveTo: 'other' });
    await lifecycle.mutateAsync({ key: 'u', revision: 4, retired: false });
    await position.mutateAsync({ urgency: 'u', after: null });
    await setDefault.mutateAsync({ key: 'u', revision: 7 });
    expect(stub.forRoute('/api/urgencies/u', 'PATCH')[0]).toMatchObject({ headers: { 'if-match': '2' }, body: { name: 'Soon' } });
    expect(stub.forRoute('/api/urgencies/u/retire', 'POST')[0]).toMatchObject({ headers: { 'if-match': '3' }, body: { move_to: 'other' } });
    expect(stub.forRoute('/api/urgencies/u/enable', 'POST')[0].headers['if-match']).toBe('4');
    expect(stub.forRoute('/api/urgencies/order/position', 'POST')[0].body).toEqual({ urgency: 'u', after: null });
    expect(stub.forRoute('/api/settings', 'PATCH')[0]).toMatchObject({ headers: { 'if-match': '7' }, body: { default_urgency: 'u' } });
    expect(invalidate).toHaveBeenCalledTimes(6);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: hubConfigKey });
  });
});
