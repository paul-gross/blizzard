import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient, type RepositorySummary } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { RepositoryDetail } from './repository-detail';

const CORE: RepositorySummary = {
  repository_id: 'repo_c',
  slug: 'core',
  name: 'Core',
  owner: 'acme',
  repo: 'core',
  base_branch: 'main',
  forge_api_url: 'https://api.github.com',
  secret_name: 'gh',
  revision: 3,
  created_at: '2026-10-01T00:00:00Z',
  created_by: 'ada',
  created_by_name: null,
  retired: false,
};
const HISTORY = {
  changes: [
    {
      id: 7,
      actor: 'ada',
      door: 'board',
      op: 'edit',
      record_key: 'repo_c',
      record_label: 'core',
      record_kind: 'repository',
      recorded_at: '2026-10-02T00:00:00Z',
      revision: 2,
      diff: [{ field: 'slug', old: 'blizzard', new: 'core' }],
    },
  ],
};
const EDITOR = { ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit'] };

describe('RepositoryDetail', () => {
  let stub: RequestClientStub;
  afterEach(() => {
    stub?.restore();
    document.querySelectorAll('dialog').forEach((d) => d.remove());
  });

  async function open(url: string) {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (path === '/api/me') return EDITOR;
      if (path === '/api/repositories/repo_c' || path === '/api/repositories/blizzard') {
        return method === 'PATCH' ? { ...CORE, slug: 'core-2', revision: 4 } : CORE;
      }
      if (path === '/api/config/changes') return HISTORY;
      return {};
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

  it('keys the history read by the id', async () => {
    await open('/admin/repositories/repo_c');
    const reads = stub.forRoute('/api/config/changes', 'GET');
    expect(reads[0].search).toContain('record_kind=repository');
    expect(reads[0].search).toContain('record_key=repo_c');
  });
});
