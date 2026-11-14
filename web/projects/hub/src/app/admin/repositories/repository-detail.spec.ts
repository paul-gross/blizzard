import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { type MeResponse, type RepositorySummary } from 'fleet';
import {
  configChangesPage,
  configChangeView,
  credentialSummary,
  forgeDetail,
  OPERATOR_ME_RESPONSE,
  type RequestClientStub,
  settle,
  stubHubClient,
} from 'fleet/testing';

import { RepositoryDetail } from './repository-detail';

const CORE: RepositorySummary = {
  forge_id: 'frg_github',
  forge_slug: 'github',
  forge_name: 'GitHub',
  warnings: [],
  credential_id: 'cred_g',
  credential_slug: 'gh-main',
  credential_name: 'gh-main',
  repository_id: 'repo_c',
  slug: 'core',
  name: 'Core',
  owner: 'acme',
  repo: 'core',
  base_branch: 'main',
  secret_name: 'gh',
  landing_policy: 'merge-on-checks',
  allow_no_ci: false,
  park_timeout_hours: null,
  revision: 3,
  created_at: '2026-10-01T00:00:00Z',
  created_by: 'ada',
  created_by_name: null,
  retired: false,
};
const HISTORY = configChangesPage({
  changes: [
    configChangeView({
      id: 7,
      actor: 'ada',
      door: 'board',
      op: 'edit',
      record_key: 'repo_c',
      record_label: 'core',
      record_kind: 'repository',
      recorded_at: '2026-10-02T00:00:00Z',
      revision: 2,
      diff: [{ field: 'slug', old: 'blizzard', new: 'core', old_label: null, new_label: null }],
    }),
  ],
});
const CREDENTIALS = [
  credentialSummary({ credential_id: 'cred_g', slug: 'gh-main', forge_id: 'frg_github' }),
  credentialSummary({ credential_id: 'cred_b', slug: 'gh-bot', forge_id: 'frg_github' }),
  credentialSummary({ credential_id: 'cred_l', slug: 'lab-main', forge_id: 'frg_lab' }),
  credentialSummary({ credential_id: 'cred_r', slug: 'gh-retired', forge_id: 'frg_github', retired: true }),
];
const EDITOR: MeResponse = { ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit'] };

describe('RepositoryDetail', () => {
  let stub: RequestClientStub;
  let record: RepositorySummary = CORE;
  afterEach(() => {
    record = CORE;
    stub?.restore();
    document.querySelectorAll('dialog').forEach((d) => d.remove());
  });

  async function open(url: string) {
    stub = stubHubClient({
      'GET /api/me': () => EDITOR,
      'GET /api/repositories/{repository}': () => record,
      'PATCH /api/repositories/{repository}': () => ({ ...record, slug: 'core-2', revision: 4 }),
      'GET /api/config/changes': () => HISTORY,
      'GET /api/forges': () => ({ forges: [forgeDetail({ forge_id: 'frg_github' }), forgeDetail({ forge_id: 'frg_lab', slug: 'lab', name: 'Lab' })] }),
      'GET /api/forges/{forge}': (_request, { forge }) => forgeDetail({ forge_id: forge }),
      'GET /api/credentials': () => ({ credentials: CREDENTIALS, bindings: [], forge_bindings: [] }),
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'admin/repositories/:key', component: RepositoryDetail }]),
      ],
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    return { harness, router: TestBed.inject(Router) };
  }

  it('opens a repository named by a former slug and rewrites the URL to its id', async () => {
    const { router } = await open('/admin/repositories/blizzard');
    expect(router.url).toBe('/admin/repositories/repo_c');
  });

  it('shows the slug as its title and the display name as a fact', async () => {
    const { harness } = await open('/admin/repositories/repo_c');
    const text = (harness.routeNativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('core');
    expect(text).toContain('Core');
  });

  it('edits by id at the shown revision, sending only the slug it changed', async () => {
    const { harness } = await open('/admin/repositories/repo_c');
    (harness.routeNativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="admin-repository-edit"]')!.click();
    await settle(harness.fixture);
    const input = document.querySelector<HTMLInputElement>('[data-testid="admin-repository-edit-field-slug"]')!;
    input.value = 'core-2';
    input.dispatchEvent(new Event('input'));
    await settle(harness.fixture);
    document.querySelector<HTMLElement>('[data-testid="admin-repository-edit-submit"]')!.click();
    await settle(harness.fixture);

    const patch = stub.forRoute('/api/repositories/repo_c', 'PATCH');
    expect(patch).toHaveLength(1);
    expect(patch[0].body).toEqual({ slug: 'core-2' });
    expect(patch[0].headers['if-match']).toBe('3');
  });

  it('shows the landing policy, and the park timeout for a parking repository', async () => {
    const { harness } = await open('/admin/repositories/repo_c');
    const text = (harness.routeNativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('merge on checks');
    expect(text).not.toContain('Park timeout');
  });

  it('sends a blank park timeout as null', async () => {
    record = { ...CORE, landing_policy: 'park', park_timeout_hours: 6 };
    const { harness } = await open('/admin/repositories/repo_c');
    expect((harness.routeNativeElement as HTMLElement).textContent).toContain('6 h');
    (harness.routeNativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="admin-repository-edit"]')!.click();
    await settle(harness.fixture);
    const input = document.querySelector<HTMLInputElement>('[data-testid="admin-repository-edit-field-park_timeout_hours"]')!;
    input.value = '';
    input.dispatchEvent(new Event('input'));
    await settle(harness.fixture);
    document.querySelector<HTMLElement>('[data-testid="admin-repository-edit-submit"]')!.click();
    await settle(harness.fixture);

    expect(stub.forRoute('/api/repositories/repo_c', 'PATCH')[0].body).toEqual({ park_timeout_hours: null });
  });

  it('keys the history read by the id', async () => {
    await open('/admin/repositories/repo_c');
    const reads = stub.forRoute('/api/config/changes', 'GET');
    expect(reads[0].search).toContain('record_kind=repository');
    expect(reads[0].search).toContain('record_key=repo_c');
  });

  const testid = (root: ParentNode, id: string) => root.querySelector<HTMLElement>(`[data-testid="${id}"]`);
  const optionLabels = () => Array.from(document.querySelectorAll<HTMLElement>('[role="option"]')).map((o) => o.textContent?.trim());

  async function choose(harness: RouterTestingHarness, field: string, label: string): Promise<void> {
    testid(document, `admin-repository-edit-field-${field}`)!.click();
    await settle(harness.fixture);
    Array.from(document.querySelectorAll<HTMLElement>('[role="option"]')).find((o) => o.textContent?.includes(label))!.click();
    await settle(harness.fixture);
  }

  it('offers only enabled credentials of the repository\'s forge, and sends the one picked', async () => {
    const { harness } = await open('/admin/repositories/repo_c');
    testid(harness.routeNativeElement as HTMLElement, 'admin-repository-edit')!.click();
    await settle(harness.fixture);
    testid(document, 'admin-repository-edit-field-credential')!.click();
    await settle(harness.fixture);
    expect(optionLabels()).toEqual(['gh-main · github.token', 'gh-bot · github.token']);
    Array.from(document.querySelectorAll<HTMLElement>('[role="option"]'))[1].click();
    await settle(harness.fixture);
    testid(document, 'admin-repository-edit-submit')!.click();
    await settle(harness.fixture);

    expect(stub.forRoute('/api/repositories/repo_c', 'PATCH')[0].body).toEqual({ credential: 'cred_b' });
  });

  it('clears the credential when the forge changes, offering only the new forge\'s', async () => {
    const { harness } = await open('/admin/repositories/repo_c');
    testid(harness.routeNativeElement as HTMLElement, 'admin-repository-edit')!.click();
    await settle(harness.fixture);
    await choose(harness, 'forge', 'Lab');
    expect(testid(document, 'admin-repository-edit-diff')?.textContent).toContain('Credential');
    testid(document, 'admin-repository-edit-field-credential')!.click();
    await settle(harness.fixture);
    expect(optionLabels()).toEqual(['lab-main · github.token']);
    Array.from(document.querySelectorAll<HTMLElement>('[role="option"]'))[0].click();
    await settle(harness.fixture);
    testid(document, 'admin-repository-edit-submit')!.click();
    await settle(harness.fixture);

    expect(stub.forRoute('/api/repositories/repo_c', 'PATCH')[0].body).toEqual({ forge: 'frg_lab', credential: 'cred_l' });
  });
});
