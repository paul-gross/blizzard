import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import type { InAppLink } from 'fleet/shell';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { commands, page } from 'vitest/browser';

import { ProjectLens, lensLink } from '../core/project-lens';
import { AdminShell } from './admin-shell';
import { ProjectDetail } from './projects/project-detail';
import { ProjectsPage } from './projects/projects-page';

/**
 * Admin → Projects' real-render proof (`web:shell-sweep`): the admin shell's strip is the
 * shorter, cyan-active sub strip with its counts in `--snow` — Changes counted by the change
 * log's `total` — and the projects list and the opened project sit in two panels with the
 * mockup's 6px gap. On All a desktop opens the first project; a phone stays on the list.
 * Run via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const PROJECTS = {
  projects: [
    { project_id: 'proj_b', slug: 'blizzard', name: 'Blizzard', description: 'The platform', revision: 4, created_at: '2026-09-20T00:00:00Z' },
    { project_id: 'proj_c', slug: 'celestial', name: 'Celestial', description: '', revision: 1, created_at: '2026-10-12T00:00:00Z' },
  ],
};

async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
}

function tokenColor(token: string): string {
  const probe = document.createElement('span');
  probe.style.color = `var(${token})`;
  document.body.appendChild(probe);
  const color = getComputedStyle(probe).color;
  probe.remove();
  return color;
}

function lensOnAll() {
  return {
    slug: signal<string | null>(null).asReadonly(),
    lensed: signal(false).asReadonly(),
    link: (link: InAppLink) => lensLink(link, null),
  };
}

describe('Admin → Projects shell sweep', () => {
  let stub: RequestClientStub;

  async function open(width: number) {
    await page.viewport(width, 800);
    await loadDesignTokens();
    stub = stubRequestClient(hubClient, (_method, path) => {
      if (path === '/api/me') return { ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit'] };
      if (path === '/api/projects') return PROJECTS;
      if (path === '/api/projects/proj_b') return PROJECTS.projects[0];
      if (path.endsWith('/source-links')) return { source_links: [] };
      if (path.endsWith('/repository-links')) return { repository_links: [] };
      if (path === '/api/work-sources') return { sources: [] };
      if (path === '/api/repositories') return { repositories: [] };
      if (path === '/api/secrets') return [];
      if (path === '/api/runners') return { runners: [] };
      if (path === '/api/config/changes') return { changes: [], total: 42 };
      if (path === '/api/chunk-counts') return { ready: 3, running: 2, done: 90, done_since: 9 };
      return {};
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([
          {
            path: 'admin',
            component: AdminShell,
            children: [
              {
                path: 'projects',
                component: ProjectsPage,
                children: [
                  { path: '', component: ProjectDetail },
                  { path: ':key', component: ProjectDetail },
                ],
              },
            ],
          },
        ]),
        { provide: ProjectLens, useValue: lensOnAll() },
      ],
    });
    const harness = await RouterTestingHarness.create();
    const root = harness.fixture.nativeElement as HTMLElement;
    root.style.display = 'flex';
    root.style.flexDirection = 'column';
    root.style.height = '800px';
    document.body.appendChild(root);
    await harness.navigateByUrl('/admin/projects');
    await settle(harness.fixture);
    await settle(harness.fixture);
    return { harness, root, router: TestBed.inject(Router) };
  }

  afterEach(async () => {
    stub?.restore();
    document.querySelectorAll('[ng-version], body > div').forEach((el) => el.remove());
    await page.viewport(1280, 800);
  });

  function strip(root: HTMLElement) {
    const nav = root.querySelector<HTMLElement>('[data-testid="admin-subnav"]')!;
    const active = nav.querySelector<HTMLElement>('[data-testid="admin-tab-projects"]')!;
    const changes = nav.querySelector<HTMLElement>('[data-testid="admin-tab-changes"] .cnt');
    return { nav, active, changes };
  }

  function noHorizontalOverflow(el: HTMLElement) {
    expect(el.scrollWidth).toBeLessThanOrEqual(el.clientWidth + 1);
  }

  it('lays list and project out as two panels and opens the first project at 1280px', async () => {
    const { harness, root, router } = await open(1280);
    expect(router.url).toBe('/admin/projects/proj_b');

    const { nav, active, changes } = strip(root);
    expect(getComputedStyle(nav).height).toBe('30px');
    expect(getComputedStyle(nav).backgroundColor).toBe(tokenColor('--panel-deep'));
    expect(getComputedStyle(active).color).toBe(tokenColor('--cyan'));
    expect(changes?.textContent?.trim()).toBe('42');
    expect(getComputedStyle(changes!).color).toBe(tokenColor('--snow'));

    const list = root.querySelector<HTMLElement>('[data-testid="admin-projects-list-panel"]')!.getBoundingClientRect();
    const detail = root.querySelector<HTMLElement>('[data-testid="admin-projects-detail-panel"]')!.getBoundingClientRect();
    expect(list.width).toBeGreaterThan(0);
    expect(detail.width).toBeGreaterThan(list.width);
    expect(Math.round(detail.left - list.right)).toBe(6);
    expect(Math.round(detail.top)).toBe(Math.round(list.top));

    const tabs = root.querySelector<HTMLElement>('[data-testid="admin-projects-detail"] [role="tablist"]')!;
    expect(getComputedStyle(tabs).height).toBe('30px');
    const sourcesCount = root.querySelector<HTMLElement>('[data-testid="admin-projects-tab-sources"] .cnt')!;
    expect(getComputedStyle(sourcesCount).color).toBe(tokenColor('--snow'));
    expect(root.querySelector('[data-testid="admin-projects-queue"]')?.textContent).toContain('3 ready · 2 running · 9 done today');
    noHorizontalOverflow(root.querySelector<HTMLElement>('[data-testid="admin-projects-detail-panel"]')!);
    harness.fixture.destroy();
  });

  it('stays on the list in its own panel at 390px', async () => {
    const { harness, root, router } = await open(390);
    expect(router.url).toBe('/admin/projects');

    const { nav, changes } = strip(root);
    expect(getComputedStyle(nav).height).toBe('30px');
    expect(changes?.textContent?.trim()).toBe('42');

    const list = root.querySelector<HTMLElement>('[data-testid="admin-projects-list-panel"]')!;
    const detail = root.querySelector<HTMLElement>('[data-testid="admin-projects-detail-panel"]')!;
    expect(list.getBoundingClientRect().width).toBeGreaterThan(0);
    expect(list.getBoundingClientRect().right).toBeLessThanOrEqual(390);
    expect(detail.getBoundingClientRect().width).toBe(0);
    expect(root.querySelector('[data-testid="projects-table-row-proj_b"]')).not.toBeNull();
    noHorizontalOverflow(list);
    harness.fixture.destroy();
  });
});
