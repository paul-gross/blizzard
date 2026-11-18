import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import {
  configChangesPage,
  OPERATOR_ME_RESPONSE,
  type RequestClientStub,
  secretView,
  settle,
  stubError,
  stubHubClient,
} from 'fleet/testing';

import { SecretDetail } from './secret-detail';

const EDITOR = { ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit' as const] };
const TOK = secretView({ name: 'tok', revision: 3 });

describe('SecretDetail', () => {
  let stub: RequestClientStub;
  let current = TOK;
  let put: () => unknown = () => secretView({ name: 'tok', revision: 4 });
  afterEach(() => {
    current = TOK;
    put = () => secretView({ name: 'tok', revision: 4 });
    stub?.restore();
    document.querySelectorAll('dialog').forEach((d) => d.remove());
  });

  async function open() {
    stub = stubHubClient({
      'GET /api/me': () => EDITOR,
      'GET /api/secrets/{name}': () => current,
      'PUT /api/secrets/{name}/value': (() => put()) as never,
      'POST /api/secrets/{name}/retire': () => secretView({ name: 'tok', retired: true }),
      'GET /api/config/changes': () => configChangesPage({ changes: [] }),
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'admin/secrets/:key', component: SecretDetail }]),
      ],
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/admin/secrets/tok');
    await settle(harness.fixture);
    const root = harness.routeNativeElement as HTMLElement;
    const q = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`);
    const openReplace = async () => {
      root.querySelector<HTMLElement>('[data-testid="admin-secret-replace"]')!.click();
      await settle(harness.fixture);
    };
    const type = async (text: string) => {
      const el = q('admin-secret-replace-field-value') as HTMLTextAreaElement;
      el.value = text;
      el.dispatchEvent(new Event('input'));
      await settle(harness.fixture);
    };
    const submit = async () => {
      q('kit-form-save')!.click();
      await settle(harness.fixture);
    };
    return { harness, root, q, openReplace, type, submit };
  }

  it('blocks a replacement with no value and sends nothing', async () => {
    const { q, openReplace, submit } = await open();
    await openReplace();
    await submit();

    expect(stub.forRoute('/api/secrets/tok/value', 'PUT')).toHaveLength(0);
    expect(q('admin-secret-replace-dialog')!.textContent).toContain('A value is required');
  });

  it('replaces against the shown revision as If-Match, then closes once the save settles', async () => {
    const { q, openReplace, type, submit } = await open();
    await openReplace();
    await type('hunter2');
    await submit();

    const calls = stub.forRoute('/api/secrets/tok/value', 'PUT');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ value: 'hunter2' });
    expect(calls[0].headers['if-match']).toBe('3');
    expect(q('admin-secret-replace-dialog')).toBeNull();
  });

  it('keeps the typed value and the baseline revision through a stale save, and shows the conflict', async () => {
    put = () => stubError(409, { type: 'urn:blizzard:problem:revision-conflict', detail: 'the secret changed since it was read' });
    const { q, openReplace, type, submit, harness } = await open();
    await openReplace();
    await type('typed');
    // The hub moves on while the form is open: the refreshed read must not replace the baseline.
    current = secretView({ name: 'tok', revision: 4 });
    await TestBed.inject(QueryClient).invalidateQueries();
    await settle(harness.fixture);
    await submit();

    const calls = stub.forRoute('/api/secrets/tok/value', 'PUT');
    expect(calls).toHaveLength(1);
    expect(calls[0].headers['if-match']).toBe('3');
    expect((q('admin-secret-replace-field-value') as HTMLTextAreaElement).value).toBe('typed');
    expect(document.querySelector('[data-testid="kit-form-line"]')!.textContent).toContain('changed since it was read');
  });

  it('retires with no If-Match', async () => {
    const { root, harness } = await open();
    root.querySelector<HTMLElement>('[data-testid="admin-secret-retire"]')!.click();
    await settle(harness.fixture);

    const calls = stub.forRoute('/api/secrets/tok/retire', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].headers['if-match']).toBeUndefined();
  });
});
