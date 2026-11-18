import { provideZonelessChangeDetection } from '@angular/core';
import { vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { ViewportService } from 'fleet';
import { TAB_TENANT_KEY } from '../../core/auth/tenant-context';
import { configChangesPage, OPERATOR_ME_RESPONSE, settle, stubError, stubHubClient, type RequestClientStub } from 'fleet/testing';
import { UrgencyDetail } from './urgency-detail';
import { hubConfigRecordKey, hubUrgencyIconKey } from '../../core/query-keys';

const urgency = { urgency_id: 'u', slug: 'soon', name: 'Soon', color: 'red', emoji: null, icon: null, retired: false, default: false, revision: 7, open_chunks: 0, place: 0, created_at: '2026-01-01T00:00:00Z', created_by: 'u' };
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
    document.querySelector<HTMLElement>('[data-testid="kit-form-save"]')!.click();
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
  const imaged = { ...urgency, icon: { digest: 'abc', media_type: 'image/png' } };
  const bytes = '\x89PNG\r\n\x1a\nbytes';
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
    client.setQueryData(hubUrgencyIconKey('ten_a', 'u', 'abc'), new Blob([Uint8Array.from(bytes, (char) => char.charCodeAt(0))], { type: 'image/png' }));
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
    document.querySelector<HTMLElement>('[data-testid="kit-form-save"]')!.click();
    await settle(harness.fixture);
    return stub.forRoute('/api/urgencies/u', 'PATCH')[0]?.body;
  }

  async function mountWithBlob(blob: Blob) {
    stub = stubHubClient({
      'GET /api/me': () => ({ ...OPERATOR_ME_RESPONSE, permissions: ['config:edit', 'chunk:control'] }),
      'GET /api/urgencies': () => ({ urgencies: [imaged] }),
      'GET /api/urgencies/{key}': () => imaged,
      'GET /api/config/changes': () => configChangesPage(),
      'PATCH /api/urgencies/{key}': () => imaged,
    });
    sessionStorage.setItem(TAB_TENANT_KEY, 'ten_a');
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(hubUrgencyIconKey('ten_a', 'u', 'abc'), blob);
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideTanStackQuery(client), provideRouter([{ path: 'admin/urgencies/:key', component: UrgencyDetail }])] });
    TestBed.inject(ViewportService).setOverride('desktop');
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/admin/urgencies/u');
    await settle(harness.fixture);
    return { harness, client };
  }

  it('holds edit while image conversion is loading and opens with the image baseline once ready', async () => {
    let resolve!: (value: ArrayBuffer) => void;
    const pending = new Promise<ArrayBuffer>((done) => { resolve = done; });
    const blob = new Blob([Uint8Array.from(bytes, (char) => char.charCodeAt(0))], { type: 'image/png' });
    vi.spyOn(blob, 'arrayBuffer').mockReturnValue(pending);
    const { harness } = await mountWithBlob(blob);
    expect((harness.routeNativeElement as HTMLElement).querySelector('[data-testid="admin-urgency-edit"]')).toBeNull();
    expect(document.querySelector('[data-testid="admin-urgency-edit-dialog"]')).toBeNull();
    resolve(Uint8Array.from(bytes, (char) => char.charCodeAt(0)).buffer);
    await vi.waitFor(async () => { await settle(harness.fixture); expect((harness.routeNativeElement as HTMLElement).querySelector('[data-testid="admin-urgency-edit"]')).not.toBeNull(); });
    (harness.routeNativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="admin-urgency-edit"]')!.click(); await settle(harness.fixture);
    expect(document.querySelector('img[alt="Selected mark"]')?.getAttribute('src')).toBe(dataUrl);
  });

  it('shows an image conversion failure and never offers an empty mark for edit', async () => {
    const blob = new Blob([Uint8Array.from(bytes, (char) => char.charCodeAt(0))], { type: 'image/png' });
    vi.spyOn(blob, 'arrayBuffer').mockRejectedValue(new Error('unreadable'));
    const { harness } = await mountWithBlob(blob);
    await vi.waitFor(async () => { await settle(harness.fixture); expect(harness.routeNativeElement?.textContent).toContain('Could not load the urgency image'); });
    expect((harness.routeNativeElement as HTMLElement).querySelector('[data-testid="admin-urgency-edit"]')).toBeNull();
    expect(document.querySelector('[data-testid="admin-urgency-edit-dialog"]')).toBeNull();
    expect(stub.forRoute('/api/urgencies/u', 'PATCH')).toHaveLength(0);
  });

  it('does not let a prior digest conversion unlock a newer icon', async () => {
    let resolve!: (value: ArrayBuffer) => void;
    const pending = new Promise<ArrayBuffer>((done) => { resolve = done; });
    const oldBlob = new Blob([Uint8Array.from(bytes, (char) => char.charCodeAt(0))], { type: 'image/png' });
    vi.spyOn(oldBlob, 'arrayBuffer').mockReturnValue(pending);
    const { harness, client } = await mountWithBlob(oldBlob);
    const nextBlob = new Blob(['new'], { type: 'image/png' });
    let resolveNext!: (value: ArrayBuffer) => void;
    vi.spyOn(nextBlob, 'arrayBuffer').mockReturnValue(new Promise<ArrayBuffer>((done) => { resolveNext = done; }));
    client.setQueryData(hubUrgencyIconKey('ten_a', 'u', 'next'), nextBlob);
    client.setQueryData(hubConfigRecordKey('urgencies', 'u'), { ...imaged, revision: 8, icon: { ...imaged.icon, digest: 'next' } });
    await settle(harness.fixture);
    resolve(Uint8Array.from(bytes, (char) => char.charCodeAt(0)).buffer); await settle(harness.fixture);
    expect((harness.routeNativeElement as HTMLElement).querySelector('[data-testid="admin-urgency-edit"]')).toBeNull();
    resolveNext(new TextEncoder().encode('new').buffer);
    await vi.waitFor(async () => { await settle(harness.fixture); expect((harness.routeNativeElement as HTMLElement).querySelector('[data-testid="admin-urgency-edit"]')).not.toBeNull(); });
    (harness.routeNativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="admin-urgency-edit"]')!.click(); await settle(harness.fixture);
    expect(document.querySelector('img[alt="Selected mark"]')?.getAttribute('src')).toBe(`data:image/png;base64,${btoa('new')}`);
  });

  it.each(['loading', 'error'] as const)('keeps an open image edit saveable when a later icon is %s', async (state) => {
    const { harness, client } = await mountWithBlob(new Blob([Uint8Array.from(bytes, (char) => char.charCodeAt(0))], { type: 'image/png' }));
    await vi.waitFor(async () => { await settle(harness.fixture); expect((harness.routeNativeElement as HTMLElement).querySelector('[data-testid="admin-urgency-edit"]')).not.toBeNull(); });
    (harness.routeNativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="admin-urgency-edit"]')!.click();
    await settle(harness.fixture);
    const input = document.querySelector<HTMLInputElement>('[data-testid="admin-urgency-edit-field-name"]')!;
    input.value = 'Later'; input.dispatchEvent(new Event('input')); await settle(harness.fixture);
    const nextBlob = new Blob(['new'], { type: 'image/png' });
    let reject!: (reason: Error) => void;
    const pending = new Promise<ArrayBuffer>((_, rejectRead) => { reject = rejectRead; });
    vi.spyOn(nextBlob, 'arrayBuffer').mockReturnValue(pending);
    client.setQueryData(hubUrgencyIconKey('ten_a', 'u', 'next'), nextBlob);
    client.setQueryData(hubConfigRecordKey('urgencies', 'u'), { ...imaged, revision: 8, icon: { ...imaged.icon, digest: 'next' } });
    await settle(harness.fixture);
    if (state === 'error') {
      reject(new Error('failed'));
      await vi.waitFor(async () => { await settle(harness.fixture); expect(harness.routeNativeElement?.textContent).toContain('Could not load the urgency image'); });
    }
    expect(document.querySelector('[data-testid="admin-urgency-edit-dialog"]')).not.toBeNull();
    expect(document.querySelector<HTMLInputElement>('[data-testid="admin-urgency-edit-field-name"]')?.value).toBe('Later');
    expect(document.querySelector('img[alt="Selected mark"]')?.getAttribute('src')).toBe(dataUrl);
    document.querySelector<HTMLElement>('[data-testid="kit-form-save"]')!.click();
    await settle(harness.fixture);
    expect(stub.forRoute('/api/urgencies/u', 'PATCH')[0]).toMatchObject({ headers: { 'if-match': '7' }, body: { name: 'Later' } });
  });

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
