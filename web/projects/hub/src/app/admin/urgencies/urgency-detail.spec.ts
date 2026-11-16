import { provideZonelessChangeDetection } from '@angular/core';
import { vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubConfigRecordKey, hubUrgencyIconKey, ViewportService, type UrgencyView } from 'fleet';
import { TAB_TENANT_KEY } from '../../core/auth/tenant-context';
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

describe('UrgencyDetail image mark', () => {
  const imaged: UrgencyView = { ...urgency, icon: { digest: 'abc', media_type: 'image/png' } };
  const bytes = 'png bytes';
  const dataUrl = `data:image/png;base64,${btoa(bytes)}`;
  let stub: RequestClientStub;
  afterEach(() => { stub?.restore(); localStorage.clear(); sessionStorage.clear(); });
  async function edit(change: () => void) {
    stub = stubHubClient({
      'GET /api/me': () => ({ ...OPERATOR_ME_RESPONSE, permissions: ['config:edit', 'chunk:control'] }),
      'GET /api/urgencies': () => ({ urgencies: [imaged] }),
      'GET /api/urgencies/{key}': () => imaged,
      'GET /api/config/changes': () => configChangesPage(),
      'PATCH /api/urgencies/{key}': () => imaged,
    });
    sessionStorage.setItem(TAB_TENANT_KEY, 'ten_a');
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(hubUrgencyIconKey('ten_a', 'u', 'abc'), new Blob([bytes], { type: 'image/png' }));
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(client), provideRouter([{ path: 'admin/urgencies/:key', component: UrgencyDetail }])] });
    TestBed.inject(ViewportService).setOverride('desktop');
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/admin/urgencies/u');
    await vi.waitFor(async () => {
      await settle(harness.fixture);
      expect((harness.routeNativeElement as HTMLElement).querySelector<HTMLButtonElement>('[data-testid="admin-urgency-edit"]')?.disabled).toBe(false);
    });
    (harness.routeNativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="admin-urgency-edit"]')!.click();
    await settle(harness.fixture);
    expect(document.querySelector('img[alt="Selected mark"]')?.getAttribute('src')).toBe(dataUrl);
    change();
    await settle(harness.fixture);
    document.querySelector<HTMLElement>('[data-testid="admin-urgency-edit-submit"]')!.click();
    await settle(harness.fixture);
    return stub.forRoute('/api/urgencies/u', 'PATCH')[0]?.body;
  }

  it('shows the shared icon as the previous data URL and saves a name-only edit without a mark', async () => {
    const body = await edit(() => {
      const input = document.querySelector<HTMLInputElement>('[data-testid="admin-urgency-edit-field-name"]')!;
      input.value = 'Later'; input.dispatchEvent(new Event('input'));
    });
    expect(body).toEqual({ name: 'Later' });
  });

  it('clears the image mark', async () => {
    const body = await edit(() => document.querySelector<HTMLElement>('[data-testid="admin-urgency-edit-field-mark-clear"]')!.click());
    expect(body).toEqual({ emoji: null, icon: null });
  });
});
