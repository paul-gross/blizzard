import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { OPERATOR_ME_RESPONSE, settle, stubError, stubHubClient, type RequestClientStub } from 'fleet/testing';
import { HubSettingsPage } from './hub-settings-page';

describe('HubSettingsPage', () => {
  let stub: RequestClientStub;
  afterEach(() => { stub?.restore(); sessionStorage.clear(); });

  it('does not read or render settings for a tenant superuser', async () => {
    const permissions = OPERATOR_ME_RESPONSE.permissions.filter((p) => p !== 'settings:admin');
    const me = { ...OPERATOR_ME_RESPONSE, role: 'superuser' as const, hub_admin: false, permissions,
      tenants: OPERATOR_ME_RESPONSE.tenants.map((tenant) => ({ ...tenant, role: 'superuser' as const, permissions })) };
    stub = stubHubClient({ 'GET /api/me': () => me });
    await TestBed.configureTestingModule({ imports: [HubSettingsPage], providers: [provideZonelessChangeDetection(), provideRouter([]),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } }))] }).compileComponents();
    const fixture = TestBed.createComponent(HubSettingsPage);
    await settle(fixture);
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Hub settings are not available');
    expect(stub.forRoute('/api/admin/settings', 'GET')).toHaveLength(0);
    expect(stub.forRoute('/api/admin/settings/changes', 'GET')).toHaveLength(0);
  });

  async function mountAuthorized(patch: NonNullable<Parameters<typeof stubHubClient>[0]['PATCH /api/admin/settings']>) {
    const permissions = [...OPERATOR_ME_RESPONSE.permissions, 'settings:admin' as const];
    const me = { ...OPERATOR_ME_RESPONSE, permissions, tenants: OPERATOR_ME_RESPONSE.tenants.map((t) => ({ ...t, permissions })) };
    const settings = { revision: 7, settings: ['alpha', 'beta'].map((key) => ({ key, value: 60, lifetime: 'pass' as const, source: 'entry' as const, revision: 7, written_at: null, written_by: null })) };
    stub = stubHubClient({ 'GET /api/me': () => me, 'GET /api/admin/settings': () => settings,
      'GET /api/admin/settings/changes': () => ({ changes: [], next_before: null }), 'PATCH /api/admin/settings': patch });
    await TestBed.configureTestingModule({ imports: [HubSettingsPage], providers: [provideZonelessChangeDetection(), provideRouter([]),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }))] }).compileComponents();
    const fixture = TestBed.createComponent(HubSettingsPage);
    await settle(fixture);
    return { fixture, el: fixture.nativeElement as HTMLElement, settings };
  }

  function button(el: HTMLElement, text: string): HTMLButtonElement {
    return [...el.querySelectorAll('button')].find((b) => b.textContent?.trim() === text)!;
  }

  it('unsets with the captured revision', async () => {
    const { fixture, el } = await mountAuthorized(() => ({ revision: 8, settings: [] }));
    button(el, 'Edit alpha').click(); await settle(fixture);
    button(el, 'Reset to default').click(); await settle(fixture);
    const request = stub.forRoute('/api/admin/settings', 'PATCH')[0];
    expect(request.body).toEqual({ unset: ['alpha'] });
    expect(request.headers['if-match']).toBe('7');
  });

  it('rejects invalid JSON without a write and retains the editor on a failed save', async () => {
    const { fixture, el } = await mountAuthorized(() => stubError(409, { detail: 'conflict' }));
    button(el, 'Edit alpha').click(); await settle(fixture);
    const input = el.querySelector('input')!;
    input.value = 'no'; input.dispatchEvent(new Event('input')); await settle(fixture);
    button(el, 'Save').click(); await settle(fixture);
    expect(el.textContent).toContain('Enter a valid JSON value.');
    expect(stub.forRoute('/api/admin/settings', 'PATCH')).toHaveLength(0);
    input.value = '75'; input.dispatchEvent(new Event('input')); await settle(fixture);
    button(el, 'Save').click(); await settle(fixture);
    expect(el.textContent).toContain('conflict');
    expect(el.textContent).toContain('Edit alpha');
  });

  it('prevents switching or cancelling while a write is pending', async () => {
    let release!: (value: { revision: number; settings: [] }) => void;
    const response = new Promise<{ revision: number; settings: [] }>((resolve) => { release = resolve; });
    const { fixture, el } = await mountAuthorized(() => response);
    button(el, 'Edit alpha').click(); await settle(fixture);
    button(el, 'Save').click();
    await vi.waitFor(() => { fixture.detectChanges(); expect(button(el, 'Edit beta').disabled).toBe(true); });
    expect(button(el, 'Cancel').disabled).toBe(true);
    button(el, 'Edit beta').click();
    expect(el.querySelector('h2')?.textContent).not.toBe('Edit beta');
    release({ revision: 8, settings: [] }); await settle(fixture);
  });
});
