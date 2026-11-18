import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import {
  configChangesPage,
  configChangeView,
  credentialSummary,
  forgeDetail,
  OPERATOR_ME_RESPONSE,
  type RequestClientStub,
  settle,
  stubError,
  stubHubClient,
  workSourceSummary,
} from 'fleet/testing';

import { WorkSourceDetail } from './work-source-detail';

const JIRA = workSourceSummary({
  forge_id: 'frg_github',
  forge_slug: 'github',
  forge_name: 'GitHub',
  work_source_id: 'wsrc_j',
  slug: 'jira',
  name: 'Acme Jira',
  provider: 'github',
  locator: 'ACME',
  secret: 'jira-token',
  edit: false,
  revision: 3,
  created_at: '2026-10-01T00:00:00Z',
  created_by: 'ada',
});
const HUB = workSourceSummary({
  name: 'hub',
  slug: 'hub',
  work_source_id: null,
  built_in: true,
  edit: false,
  created_at: null,
  created_by: null,
  provider: null,
  revision: null,
});
const HISTORY = configChangesPage({
  changes: [
    configChangeView({
      id: 7,
      actor: 'ada',
      door: 'board',
      op: 'edit',
      record_key: 'wsrc_j',
      record_label: 'jira',
      record_kind: 'work_source',
      recorded_at: '2026-10-02T00:00:00Z',
      revision: 2,
      diff: [{ field: 'slug', old: 'tracker', new: 'jira', old_label: null, new_label: null }],
    }),
  ],
});
const EDITOR = { ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit' as const] };

describe('WorkSourceDetail', () => {
  let stub: RequestClientStub;
  let current: ReturnType<typeof workSourceSummary> = JIRA;
  afterEach(() => {
    current = JIRA;
    patch = () => ({ ...JIRA, slug: 'jira-2', revision: 4 });
    stub?.restore();
    document.querySelectorAll('dialog').forEach((d) => d.remove());
  });

  let patch: () => unknown = () => ({ ...JIRA, slug: 'jira-2', revision: 4 });

  async function open(url: string) {
    stub = stubHubClient({
      'GET /api/me': () => EDITOR,
      'GET /api/work-sources/{source}': (_request, { source }) =>
        source === 'hub' ? HUB : ['wsrc_j', 'jira', 'tracker'].includes(source) ? current : stubError(404, { detail: 'no such work source' }),
      'PATCH /api/work-sources/{source}': (() => patch()) as never,
      'GET /api/config/changes': () => HISTORY,
      'POST /api/credentials/{credential}/checks': () => ({
        credential: 'gh-main',
        binding: 'github.token',
        variant: 'classic',
        variant_contradicted: false,
        granted_permissions: null,
        mint: null,
        not_evaluated: 'Branch protection, required reviews, and rulesets are not evaluated.',
        unreached_reason: null,
        uses: [],
      }),
      'GET /api/forges': () => ({ forges: [forgeDetail({ forge_id: 'frg_github' }), forgeDetail({ forge_id: 'frg_ghe', slug: 'ghe', name: 'Enterprise' })] }),
      'GET /api/credentials': () => ({
        credentials: [
          credentialSummary({ credential_id: 'cred_g', slug: 'gh-main', forge_id: 'frg_github' }),
          credentialSummary({ credential_id: 'cred_e', slug: 'ghe-main', forge_id: 'frg_ghe' }),
        ],
        bindings: [],
        forge_bindings: [],
      }),
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'admin/work-sources/:key', component: WorkSourceDetail }]),
      ],
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    return { harness, router: TestBed.inject(Router) };
  }

  it('opens a work source named by its slug and rewrites the URL to its id', async () => {
    const { router } = await open('/admin/work-sources/jira');
    expect(router.url).toBe('/admin/work-sources/wsrc_j');
  });

  it('opens a work source named by a former slug and rewrites the URL to its id', async () => {
    const { router } = await open('/admin/work-sources/tracker');
    expect(router.url).toBe('/admin/work-sources/wsrc_j');
  });

  it('leaves the built-in source at its own key', async () => {
    const { router } = await open('/admin/work-sources/hub');
    expect(router.url).toBe('/admin/work-sources/hub');
  });

  it('shows the slug as its title and the display name as a fact', async () => {
    const { harness } = await open('/admin/work-sources/wsrc_j');
    const text = (harness.routeNativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('jira');
    expect(text).toContain('Acme Jira');
  });

  it('edits by id at the shown revision, sending only the slug it changed', async () => {
    const { harness } = await open('/admin/work-sources/wsrc_j');
    (harness.routeNativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="admin-work-source-edit"]')!.click();
    await settle(harness.fixture);
    const input = document.querySelector<HTMLInputElement>('[data-testid="admin-work-source-edit-field-slug"]')!;
    input.value = 'jira-2';
    input.dispatchEvent(new Event('input'));
    await settle(harness.fixture);
    document.querySelector<HTMLElement>('[data-testid="kit-form-save"]')!.click();
    await settle(harness.fixture);

    const patch = stub.forRoute('/api/work-sources/wsrc_j', 'PATCH');
    expect(patch).toHaveLength(1);
    expect(patch[0].body).toEqual({ slug: 'jira-2' });
    expect(patch[0].headers['if-match']).toBe('3');
  });

  it('keeps the typed draft and the baseline revision through a stale save, sends once, and shows the conflict', async () => {
    patch = () => stubError(409, { type: 'urn:blizzard:problem:revision-conflict', detail: 'the work source changed since it was read' });
    const { harness } = await open('/admin/work-sources/wsrc_j');
    (harness.routeNativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="admin-work-source-edit"]')!.click();
    await settle(harness.fixture);
    const field = () => document.querySelector<HTMLInputElement>('[data-testid="admin-work-source-edit-field-name"]')!;
    field().value = 'typed';
    field().dispatchEvent(new Event('input'));
    await settle(harness.fixture);
    // The hub moves on while the form is open: the refreshed read must not replace the baseline.
    current = { ...JIRA, name: 'other', revision: 4 };
    await TestBed.inject(QueryClient).invalidateQueries();
    await settle(harness.fixture);
    document.querySelector<HTMLElement>('[data-testid="kit-form-save"]')!.click();
    await settle(harness.fixture);

    const patches = stub.forRoute('/api/work-sources/wsrc_j', 'PATCH');
    expect(patches).toHaveLength(1);
    expect(patches[0].headers['if-match']).toBe('3');
    expect(patches[0].body).toEqual({ name: 'typed' });
    expect(field().value).toBe('typed');
    expect(document.querySelector('[data-testid="kit-form-line"]')!.textContent).toContain('changed since it was read');
  });

  it('keys the history read by the id', async () => {
    await open('/admin/work-sources/wsrc_j');
    const reads = stub.forRoute('/api/config/changes', 'GET');
    expect(reads[0].search).toContain('record_kind=work_source');
    expect(reads[0].search).toContain('record_key=wsrc_j');
  });

  it('offers only credentials of the source\'s forge, clears the pick on a forge change, and sends the credential', async () => {
    const { harness } = await open('/admin/work-sources/wsrc_j');
    const testid = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`);
    const options = () => Array.from(document.querySelectorAll<HTMLElement>('[role="option"]'));
    (harness.routeNativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="admin-work-source-edit"]')!.click();
    await settle(harness.fixture);

    testid('admin-work-source-edit-field-credential')!.click();
    await settle(harness.fixture);
    expect(options().map((o) => o.textContent?.trim())).toEqual(['gh-main · github.token']);
    options()[0].click();
    await settle(harness.fixture);

    testid('admin-work-source-edit-field-forge')!.click();
    await settle(harness.fixture);
    options().find((o) => o.textContent?.includes('Enterprise'))!.click();
    await settle(harness.fixture);
    testid('admin-work-source-edit-field-credential')!.click();
    await settle(harness.fixture);
    expect(options().map((o) => o.textContent?.trim())).toEqual(['ghe-main · github.token']);
    options()[0].click();
    await settle(harness.fixture);
    testid('kit-form-save')!.click();
    await settle(harness.fixture);

    expect(stub.forRoute('/api/work-sources/wsrc_j', 'PATCH')[0].body).toEqual({ forge: 'frg_ghe', credential: 'cred_e' });
  });

  it('checks the source\'s credential against this source alone', async () => {
    current = { ...JIRA, credential_id: 'cred_g', credential_slug: 'gh-main', credential_name: 'gh-main' };
    const { harness } = await open('/admin/work-sources/wsrc_j');
    (harness.routeNativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="credential-check-run"]')!.click();
    await settle(harness.fixture);
    expect(stub.forRoute('/api/credentials/cred_g/checks', 'POST').map((request) => request.body)).toEqual([{ work_sources: ['wsrc_j'] }]);
  });

  it('offers no Check action for a source naming no credential', async () => {
    const { harness } = await open('/admin/work-sources/wsrc_j');
    expect((harness.routeNativeElement as HTMLElement).querySelector('[data-testid="admin-work-source-detail"]')).not.toBeNull();
    expect((harness.routeNativeElement as HTMLElement).querySelector('[data-testid="credential-check-run"]')).toBeNull();
  });
});
