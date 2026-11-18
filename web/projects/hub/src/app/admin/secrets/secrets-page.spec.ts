import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, secretView, settle, stubError, stubHubClient } from 'fleet/testing';

import { SecretsPage } from './secrets-page';

const EDITOR = { ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit' as const] };

describe('SecretsPage create', () => {
  let stub: RequestClientStub;
  afterEach(() => {
    stub?.restore();
    document.querySelectorAll('dialog').forEach((d) => d.remove());
  });

  async function open(post: () => unknown = () => secretView({ name: 'tok' })) {
    stub = stubHubClient({
      'GET /api/me': () => EDITOR,
      'GET /api/secrets': () => [],
      'POST /api/secrets': post as never,
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'admin/secrets', component: SecretsPage, children: [{ path: ':key', children: [] }] }]),
      ],
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/admin/secrets');
    await settle(harness.fixture);
    const root = harness.routeNativeElement as HTMLElement;
    const q = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`);
    const type = async (field: string, text: string) => {
      const el = q(`admin-secrets-create-field-${field}`) as HTMLInputElement | HTMLTextAreaElement;
      el.value = text;
      el.dispatchEvent(new Event('input'));
      await settle(harness.fixture);
    };
    const submit = async () => {
      q('kit-form-save')!.click();
      await settle(harness.fixture);
    };
    const openForm = async () => {
      root.querySelector<HTMLElement>('[data-testid="admin-secrets-new"]')!.click();
      await settle(harness.fixture);
    };
    return { q, type, submit, openForm, router: TestBed.inject(Router) };
  }

  it('refuses a create with no value, errors on Value, and sends nothing', async () => {
    const { q, type, submit, openForm } = await open();
    await openForm();
    await type('name', 'tok');
    await submit();

    expect(stub.forRoute('/api/secrets', 'POST')).toHaveLength(0);
    expect(q('admin-secrets-create-dialog')!.textContent).toContain('A value is required');
  });

  it('creates with the value as typed and no If-Match, then opens the new secret', async () => {
    const { type, submit, openForm, router } = await open();
    await openForm();
    await type('name', ' tok ');
    await type('value', 'line1\nline2\n');
    await submit();

    const posts = stub.forRoute('/api/secrets', 'POST');
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toEqual({ name: 'tok', value: 'line1\nline2\n' });
    expect(posts[0].headers['if-match']).toBeUndefined();
    expect(router.url).toBe('/admin/secrets/tok');
  });

  it('shows a taken name on Name, keeps the typed value, and keeps the form open', async () => {
    const { q, type, submit, openForm, router } = await open(() =>
      stubError(409, { type: 'urn:blizzard:problem:secret-name-taken', detail: 'name already taken' }),
    );
    await openForm();
    await type('name', 'tok');
    await type('value', 'hunter2');
    await submit();

    expect(q('admin-secrets-create-dialog')!.textContent).toContain('name already taken');
    expect((q('admin-secrets-create-field-value') as HTMLTextAreaElement).value).toBe('hunter2');
    expect(router.url).toBe('/admin/secrets');
  });
});
