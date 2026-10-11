import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import type { InAppLink } from 'fleet/shell';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { ProjectLens, lensLink } from '../core/project-lens';
import { AdminShell } from './admin-shell';

const PROJECTS = {
  projects: [
    { project_id: 'proj_b', slug: 'blizzard', name: 'Blizzard', description: '', revision: 1, created_at: '' },
    { project_id: 'proj_w', slug: 'winter', name: 'Winter', description: '', revision: 1, created_at: '' },
  ],
};
const SOURCES = { sources: [{ name: 'hub', built_in: true }, { name: 'jira' }, { name: 'gh' }] };
const REPOSITORIES = { repositories: [{ name: 'core' }, { name: 'web' }] };
const SECRETS = [{ name: 'gh-token' }];

function lensOn(slug: string | null) {
  return {
    slug: signal(slug).asReadonly(),
    lensed: signal(slug !== null).asReadonly(),
    link: (link: InAppLink) => lensLink(link, slug),
  };
}

describe('AdminShell', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  async function open(slug: string | null, permissions: readonly string[]) {
    stub = stubRequestClient(hubClient, (_method, path) => {
      if (path === '/api/me') {
        const tenants = (OPERATOR_ME_RESPONSE.tenants ?? []).map((tenant) => ({ ...tenant, permissions }));
        return { ...OPERATOR_ME_RESPONSE, permissions, tenants };
      }
      if (path === '/api/members') return [{ user_id: 'u1' }, { user_id: 'u2' }, { user_id: 'u3' }];
      if (path === '/api/projects') return PROJECTS;
      if (path === '/api/work-sources') return SOURCES;
      if (path === '/api/repositories') return REPOSITORIES;
      if (path === '/api/secrets') return SECRETS;
      if (path === '/api/projects/winter/source-links') {
        return { source_links: [{ project_id: 'proj_w', name: 'hub', linked: true, built_in: true }] };
      }
      if (path === '/api/projects/winter/repository-links') {
        return { repository_links: [{ project_id: 'proj_w', name: 'web', linked: true, revision: 1 }] };
      }
      return {};
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'admin', component: AdminShell }]),
        { provide: ProjectLens, useValue: lensOn(slug) },
      ],
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/admin');
    await settle(harness.fixture);
    return harness.routeNativeElement as HTMLElement;
  }

  const count = (el: HTMLElement, tab: string) =>
    el.querySelector(`[data-testid="admin-tab-${tab}"] .cnt`)?.textContent?.trim() ?? null;

  it('counts what each tab holds, and nothing on Changes', async () => {
    const el = await open(null, [...OPERATOR_ME_RESPONSE.permissions, 'user:manage']);
    expect(count(el, 'members')).toBe('3');
    expect(count(el, 'projects')).toBe('2');
    expect(count(el, 'work-sources')).toBe('3');
    expect(count(el, 'repositories')).toBe('2');
    expect(count(el, 'secrets')).toBe('1');
    expect(count(el, 'changes')).toBeNull();
  });

  it("counts what the lensed project links, and no members for an actor who may not read them", async () => {
    const el = await open(
      'winter',
      OPERATOR_ME_RESPONSE.permissions.filter((permission) => permission !== 'user:manage'),
    );
    expect(count(el, 'work-sources')).toBe('1');
    expect(count(el, 'repositories')).toBe('1');
    expect(count(el, 'projects')).toBe('2');
    expect(count(el, 'members')).toBeNull();
    expect(stub.forRoute('/api/members')).toHaveLength(0);
  });
});
