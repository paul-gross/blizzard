import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { ViewportService, type UrgencyView } from 'fleet';
import { OPERATOR_ME_RESPONSE, settle, stubError, stubHubClient, type RequestClientStub } from 'fleet/testing';
import { UrgenciesPage } from './urgencies-page';

const first: UrgencyView = { urgency_id: 'u1', slug: 'first', name: 'First', color: 'red', emoji: null, icon: null, retired: false, default: true, revision: 3, open_chunks: 0, place: 0, created_at: '2026-01-01T00:00:00Z', created_by: 'u' };
const second: UrgencyView = { ...first, urgency_id: 'u2', slug: 'second', name: 'Second', default: false, place: 1 };
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
});
