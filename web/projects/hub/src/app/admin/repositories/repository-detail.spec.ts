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
  repositorySummary,
  type RequestClientStub,
  settle,
  stubError,
  stubHubClient,
} from 'fleet/testing';

import { RepositoryDetail } from './repository-detail';
import { PARK_TIMEOUT_MESSAGE } from './repository.domain';

const CORE = repositorySummary({
  forge_id: 'frg_github',
  credential_id: 'cred_g',
  credential_slug: 'gh-main',
  credential_name: 'gh-main',
  name: 'Core',
  landing_policy: 'merge-on-checks',
  revision: 3,
});
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

const CHECK = {
  credential: 'gh-main', binding: 'github.token', variant: 'classic', variant_contradicted: false, granted_permissions: null, mint: null,
  not_evaluated: 'Branch protection, required reviews, and rulesets are not evaluated.', unreached_reason: null, uses: [],
};

describe('RepositoryDetail', () => {
  let stub: RequestClientStub;
  let record = CORE;
  let patch: () => unknown = () => ({ ...record, slug: 'core-2', revision: 4 });
  afterEach(() => {
    record = CORE;
    patch = () => ({ ...record, slug: 'core-2', revision: 4 });
    stub?.restore();
    document.querySelectorAll('dialog').forEach((d) => d.remove());
  });

  async function open(url: string) {
    stub = stubHubClient({
      'GET /api/me': () => ({ ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit'] }),
      'GET /api/repositories/{repository}': () => record,
      'PATCH /api/repositories/{repository}': () => patch() as never,
      'GET /api/config/changes': () => HISTORY,
      'POST /api/credentials/{credential}/checks': () => CHECK,
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
    document.querySelector<HTMLElement>('[data-testid="kit-form-save"]')!.click();
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

  const root = (harness: RouterTestingHarness) => harness.routeNativeElement as HTMLElement;

  async function startEdit(harness: RouterTestingHarness): Promise<void> {
    testid(root(harness), 'admin-repository-edit')!.click();
    await settle(harness.fixture);
  }

  async function type(harness: RouterTestingHarness, id: string, text: string): Promise<void> {
    const el = testid(document, id)!;
    const input = el instanceof HTMLInputElement ? el : el.querySelector<HTMLInputElement>('input')!;
    input.value = text;
    input.dispatchEvent(new Event('input'));
    await settle(harness.fixture);
  }

  async function save(harness: RouterTestingHarness): Promise<void> {
    testid(document, 'kit-form-save')!.click();
    await settle(harness.fixture);
  }

  it('sends the default park timeout choice as null', async () => {
    record = { ...CORE, landing_policy: 'park', park_timeout_hours: 6 };
    const { harness } = await open('/admin/repositories/repo_c');
    expect(root(harness).textContent).toContain('6 h');
    await startEdit(harness);
    testid(document, 'kit-or-default-chip')!.click();
    await settle(harness.fixture);
    expect(testid(document, 'admin-repository-edit-diff')?.textContent).toContain('Default');
    await save(harness);

    expect(stub.forRoute('/api/repositories/repo_c', 'PATCH')[0].body).toEqual({ park_timeout_hours: null });
  });

  it('marks stated hours that are not whole hours from 1 and sends nothing', async () => {
    record = { ...CORE, landing_policy: 'park', park_timeout_hours: 6 };
    const { harness } = await open('/admin/repositories/repo_c');
    await startEdit(harness);
    for (const hours of ['4.5', '0', '99999999999999999999']) {
      await type(harness, 'admin-repository-edit-field-parkTimeoutHours', hours);
      await save(harness);
      expect(document.querySelector('fleet-kit-dialog')!.textContent).toContain(PARK_TIMEOUT_MESSAGE);
    }
    expect(stub.forRoute('/api/repositories/repo_c', 'PATCH')).toHaveLength(0);
  });

  it('lands a revision conflict on the form line and keeps the typed draft open', async () => {
    patch = () =>
      stubError(409, {
        type: 'urn:blizzard:problem:revision-conflict',
        title: 'Conflict',
        instance: '/',
        detail: 'The repository changed since it was read.',
      });
    const { harness } = await open('/admin/repositories/repo_c');
    await startEdit(harness);
    await type(harness, 'admin-repository-edit-field-name', 'Core two');
    await save(harness);

    expect(testid(document, 'kit-form-line')?.textContent).toContain('changed since it was read');
    expect(testid<HTMLInputElement>(document, 'admin-repository-edit-field-name')!.value).toBe('Core two');
    expect(testid(document, 'admin-repository-edit-diff')?.textContent).toContain('Core two');
  });

  it('saves against the revision the edit opened at, even after the read refreshes', async () => {
    const { harness } = await open('/admin/repositories/repo_c');
    await startEdit(harness);
    record = { ...CORE, revision: 8 };
    await TestBed.inject(QueryClient).refetchQueries();
    await settle(harness.fixture);
    await type(harness, 'admin-repository-edit-field-name', 'Core two');
    await save(harness);

    const patches = stub.forRoute('/api/repositories/repo_c', 'PATCH');
    expect(patches).toHaveLength(1);
    expect(patches[0].headers['if-match']).toBe('3');
    expect(patches[0].body).toEqual({ name: 'Core two' });
  });

  it('marks the credential required, and refuses a save without one on its field', async () => {
    record = { ...CORE, credential_id: null, credential_slug: null, credential_name: null };
    const { harness } = await open('/admin/repositories/repo_c');
    await startEdit(harness);
    const credential = [...document.querySelectorAll('fleet-kit-field')].find((f) => f.textContent?.includes('Credential'))!;
    expect(credential.querySelector('.req')).not.toBeNull();
    await type(harness, 'admin-repository-edit-field-name', 'Core two');
    await save(harness);
    expect(credential.textContent).toContain('Required');
    expect(stub.forRoute('/api/repositories/repo_c', 'PATCH')).toHaveLength(0);
  });

  it('keys the history read by the id', async () => {
    await open('/admin/repositories/repo_c');
    const reads = stub.forRoute('/api/config/changes', 'GET');
    expect(reads[0].search).toContain('record_kind=repository');
    expect(reads[0].search).toContain('record_key=repo_c');
  });

  const testid = <E extends HTMLElement = HTMLElement>(root: ParentNode, id: string) => root.querySelector<E>(`[data-testid="${id}"]`);
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
    testid(document, 'kit-form-save')!.click();
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
    testid(document, 'kit-form-save')!.click();
    await settle(harness.fixture);

    expect(stub.forRoute('/api/repositories/repo_c', 'PATCH')[0].body).toEqual({ forge: 'frg_lab', credential: 'cred_l' });
  });

  it('checks the repository\'s credential against this repository alone', async () => {
    const { harness } = await open('/admin/repositories/repo_c');
    testid(harness.routeNativeElement as HTMLElement, 'credential-check-run')!.click();
    await settle(harness.fixture);
    expect(stub.forRoute('/api/credentials/cred_g/checks', 'POST').map((request) => request.body)).toEqual([{ repositories: ['repo_c'] }]);
  });

  it('offers no Check action for a repository naming no credential', async () => {
    record = { ...CORE, credential_id: null, credential_slug: null, credential_name: null };
    const { harness } = await open('/admin/repositories/repo_c');
    expect(testid(harness.routeNativeElement as HTMLElement, 'admin-repository-detail')).not.toBeNull();
    expect(testid(harness.routeNativeElement as HTMLElement, 'credential-check-run')).toBeNull();
  });
});
