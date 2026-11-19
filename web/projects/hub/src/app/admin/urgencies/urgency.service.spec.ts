import { provideZonelessChangeDetection } from '@angular/core';
import { vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { stubError, stubHubClient, type RequestClientStub } from 'fleet/testing';
import { injectUrgencyService } from './urgency.service';

const draft = { slug: 'soon', name: 'Soon', color: 'red', mark: '🔥' };
const baseline = { id: 'u', revision: 3, draft };
describe('urgency service', () => {
  let stub: RequestClientStub;
  afterEach(() => stub.restore());
  function setup(routes: Parameters<typeof stubHubClient>[0]) {
    stub = stubHubClient(routes);
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }))] });
    return TestBed.runInInjectionContext(injectUrgencyService);
  }
  it('keeps the captured revision on conflict and answers on the form', async () => {
    const service = setup({ 'PATCH /api/urgencies/{key}': () => stubError(409, { type: 'urn:blizzard:problem:revision-conflict', detail: 'stale edit' }) });
    expect(await service.edit(baseline, { ...draft, name: 'Later' })).toEqual({ ok: false, fields: {}, form: 'stale edit' });
    expect(stub.forRoute('/api/urgencies/u', 'PATCH')[0]).toMatchObject({ headers: { 'if-match': '3' }, body: { name: 'Later' } });
  });
  it('maps field refusals onto the mark and leaves unknown errors on the form', async () => {
    const service = setup({ 'POST /api/urgencies': () => stubError(422, { detail: [{ loc: ['body', 'icon', 'data'], msg: 'bad image', type: 'value_error' }] }) });
    expect(await service.create(draft, vi.fn())).toEqual({ ok: false, fields: { mark: 'bad image' }, form: null });
  });
  it('waits for invalidation before completing the save', async () => {
    const service = setup({ 'PATCH /api/urgencies/{key}': () => stubError(409, { detail: 'stale' }) });
    let release!: () => void;
    const refresh = new Promise<void>((resolve) => { release = resolve; });
    const invalidate = vi.spyOn(TestBed.inject(QueryClient), 'invalidateQueries').mockReturnValue(refresh);
    let done = false;
    const save = service.edit(baseline, { ...draft, name: 'Later' }).then(() => { done = true; });
    await vi.waitFor(() => expect(invalidate).toHaveBeenCalled());
    expect(done).toBe(false);
    release(); await save;
    expect(done).toBe(true);
  });

  it('refuses invalid and oversized image drafts before create or edit mutation', async () => {
    const service = setup({});
    const onCreated = vi.fn();
    const png = '\x89PNG\r\n\x1a\n';
    const oversized = `data:image/png;base64,${btoa(png + 'x'.repeat(32769 - png.length))}`;
    for (const mark of [oversized, 'data:image/png;base64,YWJj', 'data:image/svg+xml;base64,PHN2Zz4=', 'not emoji']) {
      const after = { ...draft, mark };
      expect(await service.create(after, onCreated)).toMatchObject({ ok: false, fields: { mark: expect.any(String) } });
      expect(await service.edit(baseline, after)).toMatchObject({ ok: false, fields: { mark: expect.any(String) } });
    }
    expect(onCreated).not.toHaveBeenCalled();
    expect(stub.forRoute('/api/urgencies', 'POST')).toHaveLength(0);
    expect(stub.forRoute('/api/urgencies/u', 'PATCH')).toHaveLength(0);
  });

  it('sends a valid PNG exactly at the decoded limit', async () => {
    const view = { urgency_id: 'u', slug: 'soon', name: 'Soon', color: 'red', emoji: null, icon: null, retired: false, default: false, revision: 1, open_chunks: 0, place: 0, created_at: '2026-01-01T00:00:00Z', created_by: 'u' };
    const service = setup({ 'POST /api/urgencies': () => view, 'PATCH /api/urgencies/{key}': () => view });
    const png = '\x89PNG\r\n\x1a\n';
    const data = btoa(png + 'x'.repeat(32768 - png.length));
    const after = { ...draft, mark: `data:image/png;base64,${data}` };
    const onCreated = vi.fn();
    expect(await service.create(after, onCreated)).toEqual({ ok: true });
    expect(await service.edit(baseline, after)).toEqual({ ok: true });
    expect(onCreated).toHaveBeenCalledWith('u');
    expect(stub.forRoute('/api/urgencies', 'POST')[0].body).toMatchObject({ icon: { media_type: 'image/png', data } });
    expect(stub.forRoute('/api/urgencies/u', 'PATCH')[0].body).toEqual({ emoji: null, icon: { media_type: 'image/png', data } });
  });

  it('creates slash marks and permits name-only edits with an existing slash mark', async () => {
    const view = { urgency_id: 'u', slug: 'soon', name: 'Soon', color: 'red', emoji: '/', icon: null, retired: false, default: false, revision: 1, open_chunks: 0, place: 0, created_at: '2026-01-01T00:00:00Z', created_by: 'u' };
    const service = setup({ 'POST /api/urgencies': () => view, 'PATCH /api/urgencies/{key}': () => view });
    for (const mark of ['/', '/🔥']) {
      const existing = { ...draft, mark };
      expect(await service.create(existing, vi.fn())).toEqual({ ok: true });
      expect(await service.edit({ ...baseline, draft: existing }, { ...existing, name: 'Later' })).toEqual({ ok: true });
      expect(await service.edit(baseline, existing)).toEqual({ ok: true });
    }
    expect(stub.forRoute('/api/urgencies', 'POST').map((r) => r.body)).toEqual([
      { slug: 'soon', name: 'Soon', color: 'red', emoji: '/' },
      { slug: 'soon', name: 'Soon', color: 'red', emoji: '/🔥' },
    ]);
    expect(stub.forRoute('/api/urgencies/u', 'PATCH').map((r) => r.body)).toEqual([
      { name: 'Later' }, { emoji: '/', icon: null }, { name: 'Later' }, { emoji: '/🔥', icon: null },
    ]);
  });

});
