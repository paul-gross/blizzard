import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import type { CredentialDocument, MeResponse } from 'fleet';
import {
  configChangesPage,
  configChangeView,
  credentialDocument,
  credentialSummary,
  forgeDetail,
  OPERATOR_ME_RESPONSE,
  type RequestClientStub,
  settle,
  stubError,
  stubHubClient,
} from 'fleet/testing';

import { CredentialDetail } from './credential-detail';

const BINDINGS = [{ name: 'github.token', forge_kind: 'github', settings: [{ key: 'variant', kind: 'choice' as const, choices: ['classic', 'fine-grained'] }] }];
const IN_USE: CredentialDocument = credentialDocument({
  revision: 3,
  settings: { variant: 'classic' },
  referrers: [
    { kind: 'repository', key: 'repo_c', label: 'core' },
    { kind: 'work_source', key: 'wsrc_j', label: 'issues' },
  ],
});
const HISTORY = configChangesPage({
  changes: [
    configChangeView({
      id: 9, actor: 'ada', door: 'board', op: 'edit', record_key: 'cred_g', record_label: 'gh-main', record_kind: 'credential',
      recorded_at: '2026-10-02T00:00:00Z', revision: 2,
      diff: [{ field: 'secret', old: 'gh-old', new: 'gh-token', old_label: null, new_label: null }],
    }),
  ],
});
const EDITOR: MeResponse = { ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit'] };

describe('CredentialDetail', () => {
  let stub: RequestClientStub;
  let record: CredentialDocument = IN_USE;
  afterEach(() => {
    record = IN_USE;
    stub?.restore();
    document.querySelectorAll('dialog').forEach((d) => d.remove());
  });

  async function open(url: string) {
    stub = stubHubClient({
      'GET /api/me': () => EDITOR,
      'GET /api/forges': () => ({ forges: [forgeDetail()] }),
      'GET /api/credentials': () => ({ credentials: [credentialSummary()], bindings: BINDINGS, forge_bindings: [{ forge_id: 'frg_g', bindings: ['github.token'] }] }),
      'GET /api/credentials/{credential}': () => record,
      'PATCH /api/credentials/{credential}': () => credentialSummary({ revision: 4 }),
      'POST /api/credentials/{credential}/retire': () =>
        record.referrers.length > 0
          ? stubError(409, {
              type: 'credential_in_use', title: 'Credential in use', status: 409,
              detail: 'credential gh-main is named by repository core and work source issues', referrers: [],
            })
          : credentialSummary({ retired: true, revision: 4 }),
      'POST /api/credentials/{credential}/enable': () => credentialSummary({ revision: 5 }),
      'GET /api/config/changes': () => HISTORY,
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'admin/credentials/:key', component: CredentialDetail }]),
      ],
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    return { harness, el: harness.routeNativeElement as HTMLElement, router: TestBed.inject(Router) };
  }

  const testid = (root: ParentNode, id: string) => root.querySelector<HTMLElement>(`[data-testid="${id}"]`);

  it('opens a credential named by its slug and rewrites the URL to its id', async () => {
    const { router } = await open('/admin/credentials/gh-main');
    expect(router.url).toBe('/admin/credentials/cred_g');
  });

  it('shows the authority as a read-only fact, the referrers as links, and the revisions', async () => {
    const { harness, el } = await open('/admin/credentials/cred_g');
    expect(testid(el, 'admin-credential-facts')?.textContent).toContain('secret/gh-token');
    const links = Array.from(testid(el, 'admin-credential-links')!.querySelectorAll('a')).map((a) => [a.textContent?.trim(), a.getAttribute('href')]);
    expect(links).toEqual([['core', '/admin/repositories/repo_c'], ['issues', '/admin/work-sources/wsrc_j']]);
    expect(testid(el, 'admin-credential-revisions')?.textContent).toContain('r2');

    testid(el, 'admin-credential-edit')!.click();
    await settle(harness.fixture);
    expect(testid(document, 'admin-credential-edit-field-authority')).toBeNull();
    expect(testid(document, 'admin-credential-edit-field-forge')).toBeNull();
    expect(testid(document, 'admin-credential-edit-field-binding')).toBeNull();
  });

  it('edits the variant by id at the shown revision, sending the whole settings', async () => {
    const { harness, el } = await open('/admin/credentials/cred_g');
    testid(el, 'admin-credential-edit')!.click();
    await settle(harness.fixture);
    testid(document, 'admin-credential-edit-field-setting.github.token.variant')!.click();
    await settle(harness.fixture);
    Array.from(document.querySelectorAll<HTMLElement>('[role="option"]')).find((o) => o.textContent?.includes('fine-grained'))!.click();
    await settle(harness.fixture);
    testid(document, 'admin-credential-edit-submit')!.click();
    await settle(harness.fixture);

    const patch = stub.forRoute('/api/credentials/cred_g', 'PATCH');
    expect(patch).toHaveLength(1);
    expect(patch[0].body).toEqual({ settings: { variant: 'fine-grained' } });
    expect(patch[0].headers['if-match']).toBe('3');
  });

  it('surfaces the credential_in_use refusal when retiring a credential records still name', async () => {
    const { harness, el } = await open('/admin/credentials/cred_g');
    testid(el, 'admin-credential-retire')!.click();
    await settle(harness.fixture);
    expect(stub.forRoute('/api/credentials/cred_g/retire', 'POST')).toHaveLength(1);
    expect(testid(el, 'admin-credential-action-error')?.textContent).toContain('named by repository core');
  });

  it('retires an unused credential and enables a retired one', async () => {
    record = credentialDocument({ revision: 3 });
    const first = await open('/admin/credentials/cred_g');
    testid(first.el, 'admin-credential-retire')!.click();
    await settle(first.harness.fixture);
    expect(stub.forRoute('/api/credentials/cred_g/retire', 'POST')[0].headers['if-match']).toBe('3');
    expect(testid(first.el, 'admin-credential-action-error')).toBeNull();
    stub.restore();
    TestBed.resetTestingModule();

    record = credentialDocument({ retired: true, revision: 4 });
    const second = await open('/admin/credentials/cred_g');
    testid(second.el, 'admin-credential-enable')!.click();
    await settle(second.harness.fixture);
    expect(stub.forRoute('/api/credentials/cred_g/enable', 'POST')[0].headers['if-match']).toBe('4');
  });

  it('keys the history read by the id', async () => {
    await open('/admin/credentials/cred_g');
    const reads = stub.forRoute('/api/config/changes', 'GET');
    expect(reads[0].search).toContain('record_kind=credential');
    expect(reads[0].search).toContain('record_key=cred_g');
  });
});
