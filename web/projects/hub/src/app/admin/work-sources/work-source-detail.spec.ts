import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient, type WorkSourceSummary } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { WorkSourceDetail } from './work-source-detail';

const JIRA: WorkSourceSummary = {
  work_source_id: 'wsrc_j',
  slug: 'jira',
  name: 'Acme Jira',
  provider: 'jira',
  locator: 'ACME',
  api_base: 'https://acme.atlassian.net',
  secret: 'jira-token',
  annotate: false,
  edit: false,
  revision: 3,
  created_at: '2026-10-01T00:00:00Z',
  created_by: 'ada',
  created_by_name: null,
  built_in: false,
  retired: false,
  web_base: null,
};
const HUB: WorkSourceSummary = { name: 'hub', slug: 'hub', work_source_id: null, built_in: true, annotate: false, edit: false, api_base: null, created_at: null, created_by: null, created_by_name: null, locator: null, provider: null, retired: false, revision: null, secret: null, web_base: null };
const HISTORY = {
  changes: [
    {
      id: 7,
      actor: 'ada',
      door: 'board',
      op: 'edit',
      record_key: 'wsrc_j',
      record_label: 'jira',
      record_kind: 'work_source',
      recorded_at: '2026-10-02T00:00:00Z',
      revision: 2,
      diff: [{ field: 'slug', old: 'tracker', new: 'jira' }],
    },
  ],
};
const EDITOR = { ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit'] };

describe('WorkSourceDetail', () => {
  let stub: RequestClientStub;
  afterEach(() => {
    stub?.restore();
    document.querySelectorAll('dialog').forEach((d) => d.remove());
  });

  async function open(url: string) {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (path === '/api/me') return EDITOR;
      if (path === '/api/work-sources/hub') return HUB;
      if (path === '/api/work-sources/wsrc_j' || path === '/api/work-sources/jira' || path === '/api/work-sources/tracker') {
        return method === 'PATCH' ? { ...JIRA, slug: 'jira-2', revision: 4 } : JIRA;
      }
      if (path === '/api/config/changes') return HISTORY;
      return {};
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
    document.querySelector<HTMLElement>('[data-testid="admin-work-source-edit-submit"]')!.click();
    await settle(harness.fixture);

    const patch = stub.forRoute('/api/work-sources/wsrc_j', 'PATCH');
    expect(patch).toHaveLength(1);
    expect(patch[0].body).toEqual({ slug: 'jira-2' });
    expect(patch[0].headers['if-match']).toBe('3');
  });

  it('keys the history read by the id', async () => {
    await open('/admin/work-sources/wsrc_j');
    const reads = stub.forRoute('/api/config/changes', 'GET');
    expect(reads[0].search).toContain('record_kind=work_source');
    expect(reads[0].search).toContain('record_key=wsrc_j');
  });
});
