import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { ViewportService } from 'fleet';
import {
  credentialDocument,
  credentialSummary,
  forgeDetail,
  OPERATOR_ME_RESPONSE,
  type RequestClientStub,
  settle,
  stubHubClient,
} from 'fleet/testing';

import { CredentialDetail } from './credential-detail';
import { CredentialsPage } from './credentials-page';
import { hubConfigListKey } from '../../core/query-keys';

const EDITOR = { ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit' as const] };
const LIST = {
  credentials: [credentialSummary({ referrer_count: 2 })],
  bindings: [
    { name: 'github.token', forge_kind: 'github', settings: [{ key: 'variant', kind: 'choice' as const, choices: ['classic', 'fine-grained'] }] },
    { name: 'gitlab.token', forge_kind: 'gitlab', settings: [] },
  ],
  forge_bindings: [
    { forge_id: 'frg_g', bindings: ['github.token'] },
    { forge_id: 'frg_l', bindings: ['gitlab.token'] },
  ],
};
const FORGES = {
  forges: [
    forgeDetail(),
    forgeDetail({ forge_id: 'frg_l', slug: 'gitlab', name: 'GitLab', kind: 'gitlab', api_base: 'https://gitlab.com/api/v4' }),
  ],
};

describe('CredentialsPage', () => {
  let stub: RequestClientStub;
  afterEach(() => {
    stub?.restore();
    document.querySelectorAll('dialog').forEach((d) => d.remove());
  });

  async function open(url: string, credentials: () => typeof LIST | Promise<typeof LIST> = () => LIST) {
    stub = stubHubClient({
      'GET /api/me': () => EDITOR,
      'GET /api/forges': () => FORGES,
      'GET /api/credentials': credentials,
      'GET /api/credentials/{credential}': () => credentialDocument({ credential_id: 'cred_n', slug: 'gh-ci' }),
      'POST /api/credentials': () => credentialSummary({ credential_id: 'cred_n', slug: 'gh-ci' }),
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([
          {
            path: 'admin/credentials',
            component: CredentialsPage,
            children: [
              { path: '', component: CredentialDetail },
              { path: ':key', component: CredentialDetail },
            ],
          },
        ]),
      ],
    });
    TestBed.inject(ViewportService).setOverride('desktop');
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    return { harness, el: harness.routeNativeElement as HTMLElement, router: TestBed.inject(Router) };
  }

  const testid = (root: ParentNode, id: string) =>
    root.querySelector<HTMLElement>(`[data-testid="${id}"]`);
  const options = () =>
    Array.from(
      document.querySelectorAll<HTMLElement>(
        '.cdk-overlay-pane [role="option"]:not(fleet-kit-select-row)',
      ),
    );

  async function pick(harness: RouterTestingHarness, field: string, label: string): Promise<void> {
    testid(document, `admin-credentials-create-${field.startsWith('setting-') ? field : 'field-' + field}`)!.click();
    await settle(harness.fixture);
    options().find((option) => option.textContent?.includes(label))!.click();
    await settle(harness.fixture);
  }

  async function type(harness: RouterTestingHarness, field: string, value: string): Promise<void> {
    const host = testid(document, `admin-credentials-create-${field.startsWith('setting-') ? field : 'field-' + field}`)!;
    const input = host.querySelector('input') ?? (host as HTMLInputElement);
    input.value = value;
    input.dispatchEvent(new Event('input'));
    await settle(harness.fixture);
  }

  it('lists each credential with its forge, binding, and how many records name it', async () => {
    const { el } = await open('/admin/credentials');
    const row = testid(el, 'admin-credentials-row-cred_g')!;
    expect(row.textContent).toContain('gh-main');
    expect(row.textContent).toContain('github.token');
    expect(row.textContent).toContain('used by 2 records');
  });

  it('offers only the bindings the hub allows on the picked forge', async () => {
    const { harness, el } = await open('/admin/credentials');
    testid(el, 'admin-credentials-new')!.click();
    await settle(harness.fixture);

    await pick(harness, 'forge', 'gitlab');
    testid(document, 'admin-credentials-create-field-binding')!.click();
    await settle(harness.fixture);
    expect(options().map((option) => option.textContent?.trim())).toEqual(['gitlab.token']);
    options()[0].click();
    await settle(harness.fixture);

    await pick(harness, 'forge', 'github');
    testid(document, 'admin-credentials-create-field-binding')!.click();
    await settle(harness.fixture);
    expect(options().map((option) => option.textContent?.trim())).toEqual(['github.token']);
  });

  it('creates a credential from the form, then opens it', async () => {
    const { harness, el, router } = await open('/admin/credentials');
    testid(el, 'admin-credentials-new')!.click();
    await settle(harness.fixture);
    await type(harness, 'slug', 'gh-ci');
    await pick(harness, 'forge', 'github');
    await pick(harness, 'binding', 'github.token');
    await type(harness, 'secret', 'gh-ci-token');
    await pick(harness, 'setting-github.token-variant', 'fine-grained');
    testid(document, 'kit-form-save')!.click();
    await settle(harness.fixture);

    expect(stub.forRoute('/api/credentials', 'POST')[0].body).toEqual({
      slug: 'gh-ci', forge: 'frg_g', binding: 'github.token', secret: 'gh-ci-token', settings: { variant: 'fine-grained' },
    });
    expect(router.url).toBe('/admin/credentials/cred_n');
  });

  it('reconciles delayed and refreshed settings without losing the create draft', async () => {
    let resolve!: (value: typeof LIST) => void;
    const pending = new Promise<typeof LIST>((done) => { resolve = done; });
    const { harness, el } = await open('/admin/credentials', () => pending);
    testid(el, 'admin-credentials-new')!.click();
    await settle(harness.fixture);
    await type(harness, 'slug', 'in-progress');
    resolve(LIST);
    await settle(harness.fixture);
    await pick(harness, 'forge', 'github');
    await pick(harness, 'binding', 'github.token');
    await pick(harness, 'setting-github.token-variant', 'fine-grained');
    await pick(harness, 'forge', 'gitlab');

    const client = TestBed.inject(QueryClient);
    client.setQueryData(hubConfigListKey('credentials', false), {
      ...LIST,
      bindings: [LIST.bindings[1]],
    });
    await settle(harness.fixture);
    client.setQueryData(hubConfigListKey('credentials', false), LIST);
    await settle(harness.fixture);
    await pick(harness, 'forge', 'github');
    await pick(harness, 'binding', 'github.token');
    const slug = testid(document, 'admin-credentials-create-field-slug')!;
    expect((slug.querySelector('input') ?? slug as HTMLInputElement).value).toBe('in-progress');
    expect(testid(document, 'admin-credentials-create-setting-github.token-variant')!.textContent).toContain('fine-grained');
  });

  it('blocks removed entered settings, preserves them across schema loss and binding switches, and submits them if restored', async () => {
    const { harness, el } = await open('/admin/credentials');
    testid(el, 'admin-credentials-new')!.click();
    await settle(harness.fixture);
    await type(harness, 'slug', 'gh-ci');
    await pick(harness, 'forge', 'github');
    await pick(harness, 'binding', 'github.token');
    await type(harness, 'secret', 'token');
    await pick(harness, 'setting-github.token-variant', 'fine-grained');
    const client = TestBed.inject(QueryClient);
    const update = async (bindings: typeof LIST.bindings) => {
      client.setQueryData(hubConfigListKey('credentials', false), { ...LIST, bindings });
      await settle(harness.fixture);
    };
    await update([{ ...LIST.bindings[0], settings: [] }, LIST.bindings[1]]);
    expect(testid(document, 'admin-credentials-create-settings-removed')!.textContent).toContain('variant');
    testid(document, 'kit-form-save')!.click();
    await settle(harness.fixture);
    expect(stub.forRoute('/api/credentials', 'POST')).toHaveLength(0);
    await update([LIST.bindings[1]]);
    expect(testid(document, 'admin-credentials-create-settings-unavailable')).not.toBeNull();
    testid(document, 'kit-form-save')!.click();
    await settle(harness.fixture);
    expect(stub.forRoute('/api/credentials', 'POST')).toHaveLength(0);
    await pick(harness, 'forge', 'gitlab');
    await pick(harness, 'binding', 'gitlab.token');
    expect(testid(document, 'admin-credentials-create-settings-removed')).toBeNull();
    await update(LIST.bindings);
    await pick(harness, 'forge', 'github');
    await pick(harness, 'binding', 'github.token');
    expect(testid(document, 'admin-credentials-create-setting-github.token-variant')!.textContent).toContain('fine-grained');
    testid(document, 'kit-form-save')!.click();
    await settle(harness.fixture);
    expect(stub.forRoute('/api/credentials', 'POST')[0].body).toEqual({
      slug: 'gh-ci', forge: 'frg_g', binding: 'github.token', secret: 'token', settings: { variant: 'fine-grained' },
    });
  });

  it('allows creation after the user clears a removed setting', async () => {
    const { harness, el } = await open('/admin/credentials');
    testid(el, 'admin-credentials-new')!.click();
    await settle(harness.fixture);
    await type(harness, 'slug', 'gh-ci');
    await pick(harness, 'forge', 'github');
    await pick(harness, 'binding', 'github.token');
    await type(harness, 'secret', 'token');
    await pick(harness, 'setting-github.token-variant', 'classic');
    TestBed.inject(QueryClient).setQueryData(hubConfigListKey('credentials', false), {
      ...LIST, bindings: [{ ...LIST.bindings[0], settings: [] }, LIST.bindings[1]],
    });
    await settle(harness.fixture);
    await pick(harness, 'setting-github.token-variant', 'Not set');
    expect(testid(document, 'admin-credentials-create-settings-removed')).toBeNull();
    testid(document, 'kit-form-save')!.click();
    await settle(harness.fixture);
    expect(stub.forRoute('/api/credentials', 'POST')[0].body).toEqual({
      slug: 'gh-ci', forge: 'frg_g', binding: 'github.token', secret: 'token',
    });
  });
});
