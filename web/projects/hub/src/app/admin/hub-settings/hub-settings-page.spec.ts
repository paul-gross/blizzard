import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { OPERATOR_ME_RESPONSE, settle, stubHubClient, type RequestClientStub } from 'fleet/testing';
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
});
