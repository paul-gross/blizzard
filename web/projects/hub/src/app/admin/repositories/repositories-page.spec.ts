import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { ViewportService } from 'fleet';
import {
  configChangesPage,
  credentialSummary,
  forgeDetail,
  OPERATOR_ME_RESPONSE,
  repositorySummary,
  type RequestClientStub,
  settle,
  stubError,
  stubHubClient,
} from 'fleet/testing';

import { RepositoriesPage } from './repositories-page';
import { PARK_TIMEOUT_MESSAGE } from './repository.domain';

const CREDENTIALS = [
  credentialSummary({ credential_id: 'cred_g', slug: 'gh-main', forge_id: 'frg_github' }),
  credentialSummary({ credential_id: 'cred_l', slug: 'lab-main', forge_id: 'frg_lab' }),
];

describe('RepositoriesPage create', () => {
  let stub: RequestClientStub;
  let create: () => unknown = () => repositorySummary({ slug: 'core' });
  afterEach(() => {
    stub?.restore();
    create = () => repositorySummary({ slug: 'core' });
    document.querySelectorAll('dialog').forEach((d) => d.remove());
  });

  async function open() {
    stub = stubHubClient({
      'GET /api/me': () => ({ ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit'] }),
      'GET /api/repositories': () => ({ repositories: [] }),
      'POST /api/repositories': () => create() as never,
      'GET /api/config/changes': () => configChangesPage(),
      'GET /api/forges': () => ({
        forges: [forgeDetail({ forge_id: 'frg_github' }), forgeDetail({ forge_id: 'frg_lab', slug: 'lab', name: 'Lab' })],
      }),
      'GET /api/forges/{forge}': (_request, { forge }) => forgeDetail({ forge_id: forge }),
      'GET /api/credentials': () => ({ credentials: CREDENTIALS, bindings: [], forge_bindings: [] }),
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([
          { path: 'admin/repositories', component: RepositoriesPage, children: [{ path: '', children: [] }, { path: ':key', children: [] }] },
        ]),
      ],
    });
    TestBed.inject(ViewportService).setOverride('desktop');
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/admin/repositories');
    await settle(harness.fixture);
    q(harness.routeNativeElement as HTMLElement, 'admin-repositories-new')!.click();
    await settle(harness.fixture);
    return harness;
  }

  const q = <E extends HTMLElement = HTMLElement>(root: ParentNode, id: string) =>
    root.querySelector<E>(`[data-testid="${id}"]`);
  const field = (label: string) =>
    [...document.querySelectorAll('fleet-kit-field')].find((f) => f.textContent?.trim().startsWith(label))!;
  const options = () => [...document.querySelectorAll<HTMLElement>('.cdk-overlay-pane [role="option"]:not(fleet-kit-select-row)')];

  async function type(harness: RouterTestingHarness, key: string, text: string): Promise<void> {
    const el = q(document, `admin-repositories-create-field-${key}`)!;
    const input = el instanceof HTMLInputElement ? el : el.querySelector<HTMLInputElement>('input')!;
    input.value = text;
    input.dispatchEvent(new Event('input'));
    await settle(harness.fixture);
  }

  async function choose(harness: RouterTestingHarness, key: string, label: string): Promise<void> {
    q(document, `admin-repositories-create-field-${key}`)!.click();
    await settle(harness.fixture);
    options().find((o) => o.textContent?.includes(label))!.click();
    await settle(harness.fixture);
  }

  async function save(harness: RouterTestingHarness): Promise<void> {
    q(document, 'kit-form-save')!.click();
    await settle(harness.fixture);
  }

  async function fill(harness: RouterTestingHarness): Promise<void> {
    await type(harness, 'slug', 'core');
    await choose(harness, 'forge', 'GitHub');
    await type(harness, 'owner', 'paul-gross');
    await type(harness, 'repo', 'core');
    await type(harness, 'baseBranch', 'main');
    await choose(harness, 'credential', 'gh-main');
  }

  it('marks forge and credential required before anything is typed', async () => {
    await open();
    for (const label of ['Slug', 'Forge', 'Owner', 'Repository', 'Base branch', 'Credential']) {
      expect(field(label).querySelector('.req')).not.toBeNull();
    }
    expect(field('Name').querySelector('.req')).toBeNull();
  });

  it('refuses a save with no forge on the forge field and sends nothing', async () => {
    const harness = await open();
    await type(harness, 'slug', 'core');
    await save(harness);
    expect(field('Forge').textContent).toContain('Required');
    expect(stub.forRoute('/api/repositories', 'POST')).toHaveLength(0);
  });

  it('offers the chosen forge\'s credentials and clears the credential when the forge changes', async () => {
    const harness = await open();
    await choose(harness, 'forge', 'GitHub');
    await choose(harness, 'credential', 'gh-main');
    await choose(harness, 'forge', 'Lab');
    q(document, 'admin-repositories-create-field-credential')!.click();
    await settle(harness.fixture);
    expect(options().map((o) => o.textContent?.trim())).toEqual(['lab-main · github.token']);
  });

  it('creates with Allow no CI off and a blank name and policy left out, then opens the record', async () => {
    const harness = await open();
    await fill(harness);
    await save(harness);
    const posts = stub.forRoute('/api/repositories', 'POST');
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toEqual({
      slug: 'core',
      forge: 'frg_github',
      owner: 'paul-gross',
      repo: 'core',
      base_branch: 'main',
      credential: 'cred_g',
      allow_no_ci: false,
    });
    expect(TestBed.inject(Router).url).toBe('/admin/repositories/core');
    expect(document.querySelector('fleet-kit-dialog')).toBeNull();
  });

  it('offers the park timeout as the default choice, and marks stated hours that are not whole hours', async () => {
    const harness = await open();
    await fill(harness);
    expect(q(document, 'admin-repositories-create-field-parkTimeoutHours')).toBeNull();
    await choose(harness, 'landingPolicy', 'park');
    expect(q(document, 'kit-or-default-chip')!.getAttribute('aria-pressed')).toBe('true');
    q(document, 'kit-or-default-chip')!.click();
    await settle(harness.fixture);
    await type(harness, 'parkTimeoutHours', '4.5');
    await save(harness);
    expect(field('Park timeout').textContent).toContain(PARK_TIMEOUT_MESSAGE);
    expect(stub.forRoute('/api/repositories', 'POST')).toHaveLength(0);

    q(document, 'kit-or-default-chip')!.click();
    await settle(harness.fixture);
    await save(harness);
    expect(stub.forRoute('/api/repositories', 'POST')[0].body).toMatchObject({ landing_policy: 'park', park_timeout_hours: null });
  });

  it('lands a taken slug on the slug field and keeps the dialog open', async () => {
    create = () =>
      stubError(409, { type: 'urn:blizzard:problem:repository-slug-taken', title: 'Conflict', instance: '/', detail: 'Slug core is taken.' });
    const harness = await open();
    await fill(harness);
    await save(harness);
    expect(field('Slug').textContent).toContain('Slug core is taken.');
    expect(q<HTMLInputElement>(document, 'admin-repositories-create-field-slug')!.value).toBe('core');
  });
});
