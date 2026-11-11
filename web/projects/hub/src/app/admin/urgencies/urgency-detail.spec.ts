import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubConfigRecordKey, ViewportService, type UrgencyView } from 'fleet';
import { configChangesPage, OPERATOR_ME_RESPONSE, settle, stubError, stubHubClient, type RequestClientStub } from 'fleet/testing';
import { UrgencyDetail } from './urgency-detail';

const urgency: UrgencyView = { urgency_id: 'u', slug: 'soon', name: 'Soon', color: 'red', emoji: null, icon: null, retired: false, default: false, revision: 7, open_chunks: 0, place: 0, created_at: '2026-01-01T00:00:00Z', created_by: 'u' };
describe('UrgencyDetail writes', () => {
  let stub: RequestClientStub;
  afterEach(() => { stub?.restore(); localStorage.clear(); });
  async function open(retired = false, refuse = false) {
    stub = stubHubClient({
      'GET /api/me': () => ({ ...OPERATOR_ME_RESPONSE, permissions: ['config:edit', 'chunk:control'] }),
      'GET /api/urgencies': () => ({ urgencies: [urgency] }),
      'GET /api/urgencies/{key}': () => ({ ...urgency, retired }),
      'GET /api/config/changes': () => configChangesPage(),
      'PATCH /api/urgencies/{key}': () => refuse ? stubError(409, { detail: 'stale edit' }) : urgency,
      'POST /api/urgencies/{key}/enable': () => refuse ? stubError(409, { detail: 'enable refused' }) : urgency,
      'POST /api/urgencies/{key}/retire': () => ({ ...urgency, retired: true, moved_chunks: 0 }),
    });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })), provideRouter([{ path: 'admin/urgencies/:key', component: UrgencyDetail }])] });
    TestBed.inject(ViewportService).setOverride('desktop');
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/admin/urgencies/u');
    await settle(harness.fixture);
    return harness;
  }
  it('edits with the shown revision and surfaces a refusal', async () => {
    const harness = await open(false, true);
    (harness.routeNativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="admin-urgency-edit"]')!.click();
    await settle(harness.fixture);
    TestBed.inject(QueryClient).setQueryData(hubConfigRecordKey('urgencies', 'u'), { ...urgency, revision: 8 });
    await settle(harness.fixture);
    const input = document.querySelector<HTMLInputElement>('[data-testid="admin-urgency-edit-field-name"]')!;
    input.value = 'Later'; input.dispatchEvent(new Event('input'));
    await settle(harness.fixture);
    document.querySelector<HTMLElement>('[data-testid="admin-urgency-edit-submit"]')!.click();
    await settle(harness.fixture);
    expect(stub.forRoute('/api/urgencies/u', 'PATCH')[0]).toMatchObject({ headers: { 'if-match': '7' }, body: { name: 'Later' } });
    expect(document.body.textContent).toContain('stale edit');
  });
  it('enables using the shown revision and surfaces a refusal', async () => {
    const harness = await open(true, true);
    (harness.routeNativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="admin-urgency-enable"]')!.click();
    await settle(harness.fixture);
    expect(stub.forRoute('/api/urgencies/u/enable', 'POST')[0].headers['if-match']).toBe('7');
    expect(harness.routeNativeElement?.textContent).toContain('enable refused');
  });
  it('confirms retirement even when no chunks need moving', async () => {
    const harness = await open();
    (harness.routeNativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="admin-urgency-retire"]')!.click();
    await settle(harness.fixture);
    expect(stub.forRoute('/api/urgencies/u/retire', 'POST')).toHaveLength(0);
    expect(document.querySelector('[data-testid="admin-urgency-retire-dialog"]')?.textContent).toContain('0 open chunks');
  });
});
