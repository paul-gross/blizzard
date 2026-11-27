import { Component, provideZonelessChangeDetection } from '@angular/core';
import { vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { ViewportService } from 'fleet/viewport';
import { OPERATOR_ME_RESPONSE, settle, stubError, stubHubClient, type RequestClientStub } from 'fleet/testing';
import { UrgenciesPage } from './urgencies-page';

@Component({ selector: 'app-test-urgency', template: '' })
class TestUrgency {}

const first = { urgency_id: 'u1', slug: 'first', name: 'First', color: 'red', emoji: null, icon: null, retired: false, default: true, revision: 3, open_chunks: 0, place: 0, created_at: '2026-01-01T00:00:00Z', created_by: 'u' };
const second = { ...first, urgency_id: 'u2', slug: 'second', name: 'Second', default: false, place: 1 };
describe('UrgenciesPage default', () => {
  let stub: RequestClientStub;
  afterEach(() => { stub?.restore(); localStorage.clear(); });
  it('sends the settings revision and displays a refusal', async () => {
    stub = stubHubClient({
      'GET /api/me': () => ({ ...OPERATOR_ME_RESPONSE, permissions: ['config:edit'] }),
      'GET /api/urgencies': () => ({ urgencies: [first, second] }),
      'GET /api/settings': () => ({ default_urgency: { urgency_id: 'u1', slug: 'first' }, revision: 8, updated_at: '2026-01-01T00:00:00Z', updated_by: 'u' }),
      'PATCH /api/settings': () => stubError(409, { detail: 'default refused' }),
    });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })), provideRouter([{ path: 'admin/urgencies', component: UrgenciesPage }])] });
    TestBed.inject(ViewportService).setOverride('desktop');
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/admin/urgencies');
    await settle(harness.fixture);
    (harness.routeNativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="admin-urgencies-default"]')!.click();
    await settle(harness.fixture);
    document.querySelector<HTMLElement>('[role="option"][title="Second"]')!.click();
    await settle(harness.fixture);
    expect(stub.forRoute('/api/settings', 'PATCH')[0]).toMatchObject({ headers: { 'if-match': '8' }, body: { default_urgency: 'u2' } });
    expect(harness.routeNativeElement?.textContent).toContain('default refused');
  });
  it('validates the typed create and maps a refused slug without closing the dialog', async () => {
    stub = stubHubClient({
      'GET /api/me': () => ({ ...OPERATOR_ME_RESPONSE, permissions: ['config:edit'] }),
      'GET /api/urgencies': () => ({ urgencies: [first] }),
      'GET /api/settings': () => ({ default_urgency: { urgency_id: 'u1', slug: 'first' }, revision: 8, updated_at: '2026-01-01T00:00:00Z', updated_by: 'u' }),
      'POST /api/urgencies': () => stubError(422, { detail: [{ loc: ['body', 'slug'], msg: 'already taken', type: 'value_error' }] }),
    });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })), provideRouter([{ path: 'admin/urgencies', component: UrgenciesPage }])] });
    TestBed.inject(ViewportService).setOverride('desktop');
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/admin/urgencies'); await settle(harness.fixture);
    (harness.routeNativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="admin-urgencies-new"]')!.click();
    await settle(harness.fixture);
    const save = document.querySelector<HTMLButtonElement>('[data-testid="kit-form-save"]')!;
    save.click(); await settle(harness.fixture);
    expect(stub.forRoute('/api/urgencies', 'POST')).toHaveLength(0);
    for (const [field, value] of [['slug', 'first'], ['name', 'First']]) {
      const input = document.querySelector<HTMLInputElement>(`[data-testid="admin-urgencies-create-field-${field}"]`)!;
      input.value = value; input.dispatchEvent(new Event('input'));
    }
    await settle(harness.fixture); save.click(); await settle(harness.fixture);
    expect(stub.forRoute('/api/urgencies', 'POST')[0].body).toEqual({ slug: 'first', name: 'First' });
    expect(document.body.textContent).toContain('already taken');
    expect(document.querySelector('[data-testid="admin-urgencies-create-dialog"]')).not.toBeNull();
  });

  it('selects the returned ID only after the create invalidation settles', async () => {
    const created = { ...second, urgency_id: 'new-id', slug: 'new-slug', name: 'New urgency' };
    stub = stubHubClient({
      'GET /api/me': () => ({ ...OPERATOR_ME_RESPONSE, permissions: ['config:edit'] }),
      'GET /api/urgencies': () => ({ urgencies: [first, created] }),
      'GET /api/settings': () => ({ default_urgency: { urgency_id: 'u1', slug: 'first' }, revision: 8, updated_at: '2026-01-01T00:00:00Z', updated_by: 'u' }),
      'POST /api/urgencies': () => created,
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(client), provideRouter([{ path: 'admin/urgencies', component: UrgenciesPage, children: [{ path: ':key', component: TestUrgency }] }])] });
    TestBed.inject(ViewportService).setOverride('desktop');
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/admin/urgencies'); await settle(harness.fixture);
    (harness.routeNativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="admin-urgencies-new"]')!.click(); await settle(harness.fixture);
    for (const [field, value] of [['slug', 'new-slug'], ['name', 'New urgency']]) {
      const input = document.querySelector<HTMLInputElement>(`[data-testid="admin-urgencies-create-field-${field}"]`)!;
      input.value = value; input.dispatchEvent(new Event('input'));
    }
    await settle(harness.fixture);
    let release!: () => void;
    const refreshed = new Promise<void>((resolve) => { release = resolve; });
    const invalidate = vi.spyOn(client, 'invalidateQueries').mockReturnValue(refreshed);
    document.querySelector<HTMLElement>('[data-testid="kit-form-save"]')!.click();
    await vi.waitFor(() => expect(invalidate).toHaveBeenCalled());
    expect(TestBed.inject(Router).url).toBe('/admin/urgencies');
    expect(document.querySelector('[data-testid="admin-urgencies-create-dialog"]')).not.toBeNull();
    release();
    await vi.waitFor(async () => { await settle(harness.fixture); expect(TestBed.inject(Router).url).toBe('/admin/urgencies/new-id'); });
    expect(document.querySelector('[data-testid="admin-urgencies-create-dialog"]')).toBeNull();
  });

  it('refuses a malformed slug locally and holds create while a custom color is uncommitted', async () => {
    stub = stubHubClient({
      'GET /api/me': () => ({ ...OPERATOR_ME_RESPONSE, permissions: ['config:edit'] }),
      'GET /api/urgencies': () => ({ urgencies: [first] }),
      'GET /api/settings': () => ({ default_urgency: { urgency_id: 'u1', slug: 'first' }, revision: 8, updated_at: '2026-01-01T00:00:00Z', updated_by: 'u' }),
      'POST /api/urgencies': () => first,
    });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })), provideRouter([{ path: 'admin/urgencies', component: UrgenciesPage, children: [{ path: ':key', component: TestUrgency }] }])] });
    TestBed.inject(ViewportService).setOverride('desktop');
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/admin/urgencies'); await settle(harness.fixture);
    (harness.routeNativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="admin-urgencies-new"]')!.click(); await settle(harness.fixture);
    const slug = document.querySelector<HTMLInputElement>('[data-testid="admin-urgencies-create-field-slug"]')!;
    const name = document.querySelector<HTMLInputElement>('[data-testid="admin-urgencies-create-field-name"]')!;
    slug.value = 'Bad slug!'; slug.dispatchEvent(new Event('input'));
    name.value = 'Soon'; name.dispatchEvent(new Event('input')); await settle(harness.fixture);
    document.querySelector<HTMLElement>('[data-testid="kit-form-save"]')!.click(); await settle(harness.fixture);
    expect(stub.forRoute('/api/urgencies', 'POST')).toHaveLength(0);
    expect(document.body.textContent).toContain('lowercase letters or digits');
    slug.value = 'soon'; slug.dispatchEvent(new Event('input')); await settle(harness.fixture);
    const color = document.querySelector<HTMLInputElement>('[data-testid="project-color-custom-input"]')!;
    color.value = 'bad'; color.dispatchEvent(new Event('input')); await settle(harness.fixture);
    expect(color.getAttribute('aria-invalid')).toBe('true');
    expect(document.querySelector<HTMLButtonElement>('[data-testid="kit-form-save"]')?.disabled).toBe(true);
    document.querySelector('fleet-kit-form form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await settle(harness.fixture);
    expect(stub.forRoute('/api/urgencies', 'POST')).toHaveLength(0);
    color.value = '#ff8800'; color.dispatchEvent(new Event('input')); await settle(harness.fixture);
    document.querySelector<HTMLElement>('[data-testid="kit-form-save"]')!.click(); await settle(harness.fixture);
    expect(stub.forRoute('/api/urgencies', 'POST')[0].body).toEqual({ slug: 'soon', name: 'Soon', color: '#ff8800' });
  });

  it('holds create and displays an error for an oversized image until the mark is corrected', async () => {
    stub = stubHubClient({
      'GET /api/me': () => ({ ...OPERATOR_ME_RESPONSE, permissions: ['config:edit'] }),
      'GET /api/urgencies': () => ({ urgencies: [first] }),
      'GET /api/settings': () => ({ default_urgency: { urgency_id: 'u1', slug: 'first' }, revision: 8, updated_at: '2026-01-01T00:00:00Z', updated_by: 'u' }),
      'POST /api/urgencies': () => first,
    });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })), provideRouter([{ path: 'admin/urgencies', component: UrgenciesPage, children: [{ path: ':key', component: TestUrgency }] }])] });
    TestBed.inject(ViewportService).setOverride('desktop');
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/admin/urgencies'); await settle(harness.fixture);
    (harness.routeNativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="admin-urgencies-new"]')!.click(); await settle(harness.fixture);
    for (const [field, value] of [['slug', 'soon'], ['name', 'Soon']]) {
      const input = document.querySelector<HTMLInputElement>(`[data-testid="admin-urgencies-create-field-${field}"]`)!;
      input.value = value; input.dispatchEvent(new Event('input'));
    }
    const image = document.querySelector<HTMLInputElement>('[data-testid="admin-urgencies-create-field-mark-image"]')!;
    Object.defineProperty(image, 'files', { configurable: true, value: [new File([new Uint8Array(635006)], 'large.png', { type: 'image/png' })] });
    image.dispatchEvent(new Event('change')); await settle(harness.fixture);
    expect(document.body.textContent).toContain('Image must be at most 32768 bytes.');
    expect(document.querySelector<HTMLButtonElement>('[data-testid="kit-form-save"]')?.disabled).toBe(true);
    document.querySelector('fleet-kit-form form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await settle(harness.fixture);
    expect(stub.forRoute('/api/urgencies', 'POST')).toHaveLength(0);
    const mark = document.querySelector<HTMLInputElement>('[data-testid="admin-urgencies-create-field-mark"]')!;
    mark.value = '🔥'; mark.dispatchEvent(new Event('input')); await settle(harness.fixture);
    expect(document.querySelector<HTMLButtonElement>('[data-testid="kit-form-save"]')?.disabled).toBe(false);
    document.querySelector<HTMLElement>('[data-testid="kit-form-save"]')!.click(); await settle(harness.fixture);
    expect(stub.forRoute('/api/urgencies', 'POST')[0].body).toEqual({ slug: 'soon', name: 'Soon', emoji: '🔥' });
  });

});
