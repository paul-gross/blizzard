import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import type { InAppLink } from 'fleet/shell';
import {
  chunkCountsView,
  configChangesPage,
  OPERATOR_ME_RESPONSE,
  projectView,
  type RequestClientStub,
  settle,
  stubHubClient,
} from 'fleet/testing';
import { commands, page } from 'vitest/browser';

import { ProjectLens, lensLink } from '../core/project-lens';
import { AdminShell } from './admin-shell';
import { ProjectDetail } from './projects/project-detail';
import { ProjectsPage } from './projects/projects-page';
import { UrgenciesPage } from './urgencies/urgencies-page';
import { UrgencyDetail } from './urgencies/urgency-detail';

/**
 * Admin → Projects' real-render proof (`web:shell-sweep`): the admin shell's strip is the
 * shorter, cyan-active sub strip with its counts in `--snow` — Changes counted by the change
 * log's `total` — and the projects list and the opened project sit in two panels with the
 * mockup's 6px gap. On All a desktop opens the first project; a phone stays on the list.
 * Run via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const PROJECTS = {
  projects: [
    projectView({ project_id: 'proj_b', slug: 'blizzard', name: 'Blizzard', description: 'The platform', revision: 4, created_at: '2026-09-20T00:00:00Z' }),
    projectView({ project_id: 'proj_c', slug: 'celestial', name: 'Celestial', description: '', revision: 1, created_at: '2026-10-12T00:00:00Z' }),
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
    stub = stubHubClient({
      'GET /api/me': () => ({ ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit'] }),
      'GET /api/members': () => [],
      'GET /api/projects': () => PROJECTS,
      'GET /api/projects/{project}': () => PROJECTS.projects[0],
      'GET /api/projects/{project}/source-links': () => ({ source_links: [] }),
      'GET /api/projects/{project}/repository-links': () => ({ repository_links: [] }),
      'GET /api/work-sources': () => ({ sources: [] }),
      'GET /api/repositories': () => ({ repositories: [] }),
      'GET /api/secrets': () => [],
      'GET /api/runners': () => ({ runners: [] }),
      'GET /api/config/changes': () => configChangesPage({ total: 42 }),
      'GET /api/chunk-counts': () => chunkCountsView({ ready: 3, running: 2, done: 90, done_since: 9 }),
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

describe('Admin → Urgencies shell sweep', () => {
  let stub: RequestClientStub;
  const slug = 'a-very-long-urgency-slug-for-testing-phone-layout-without-horizontal-overflow';
  const urgency = {
    urgency_id: 'urg_test', slug, name: 'A long urgency name for the phone', color: 'red', emoji: '🔥', icon: null,
    retired: false, default: false, revision: 3, open_chunks: 4, place: 0,
    created_at: '2026-01-01T00:00:00Z', created_by: 'operator',
  };
  const defaultUrgency = { ...urgency, urgency_id: 'urg_default', slug: 'normal', name: 'Normal', default: true, open_chunks: 0, place: 1 };

  async function openUrgencies(width: number) {
    await page.viewport(width, 800);
    await loadDesignTokens();
    stub = stubHubClient({
      'GET /api/me': () => ({ ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit', 'chunk:control'] }),
      'GET /api/members': () => [],
      'GET /api/projects': () => ({ projects: [] }),
      'GET /api/work-sources': () => ({ sources: [] }),
      'GET /api/repositories': () => ({ repositories: [] }),
      'GET /api/forges': () => ({ forges: [] }),
      'GET /api/secrets': () => [],
      'GET /api/urgencies': () => ({ urgencies: [urgency, defaultUrgency] }),
      'GET /api/urgencies/{key}': (_request, { key }) => key === 'urg_retired' ? { ...urgency, urgency_id: 'urg_retired', retired: true } : urgency,
      'GET /api/settings': () => ({ default_urgency: { urgency_id: 'urg_default', slug: 'normal' }, revision: 1, updated_at: '2026-01-01T00:00:00Z', updated_by: 'operator' }),
      'GET /api/config/changes': () => configChangesPage({ total: 0 }),
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'admin', component: AdminShell, children: [
          { path: 'urgencies', component: UrgenciesPage, children: [
            { path: '', component: UrgencyDetail }, { path: ':key', component: UrgencyDetail },
          ] },
        ] }]),
        { provide: ProjectLens, useValue: lensOnAll() },
      ],
    });
    const harness = await RouterTestingHarness.create();
    const root = harness.fixture.nativeElement as HTMLElement;
    root.style.cssText = 'display:flex;flex-direction:column;height:800px;min-width:0';
    document.body.appendChild(root);
    await harness.navigateByUrl('/admin/urgencies');
    await settle(harness.fixture);
    await settle(harness.fixture);
    return { harness, root };
  }
  afterEach(async () => {
    stub?.restore();
    document.querySelectorAll('[ng-version], body > div').forEach((el) => el.remove());
    await page.viewport(1280, 800);
  });
  function fits(el: HTMLElement) {
    expect(el.scrollWidth, `${el.className} overflows`).toBeLessThanOrEqual(el.clientWidth + 1);
  }
  it.each([320, 390])('renders real list and detail read-only without overflow at %ipx', async (width) => {
    const { harness, root } = await openUrgencies(width);
    expect(root.querySelector('[data-testid="admin-urgencies-new"]')).toBeNull();
    expect(root.querySelector('[data-testid="admin-urgencies-default"]')).toBeNull();
    expect(root.querySelector('.drag-handle')).toBeNull();
    const listHints = root.querySelector<HTMLElement>('[data-testid="admin-urgencies-cli"]')!;
    expect(listHints.textContent).toContain('urgency create <slug>');
    expect(listHints.textContent).toContain('settings edit --default-urgency <slug>');
    expect(listHints.textContent).toContain('urgency position <slug> --first');
    fits(listHints);
    fits(root.querySelector<HTMLElement>('.kmd-list')!);
    await TestBed.inject(Router).navigateByUrl('/admin/urgencies/urg_test');
    for (let i = 0; i < 10; i++) {
      harness.fixture.detectChanges();
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    expect(root.querySelector('[data-testid="admin-urgency-edit"]')).toBeNull();
    expect(root.querySelector('[data-testid="admin-urgency-retire"]')).toBeNull();
    const hints = root.querySelector<HTMLElement>('[data-testid="admin-urgency-cli-actions"]')!;
    expect(hints.textContent).toContain(`urgency edit ${slug}`);
    expect(hints.textContent).toContain(`urgency retire ${slug} --move-to <slug>`);
    expect(hints.textContent).toContain(`settings edit --default-urgency ${slug}`);
    expect(hints.textContent).toContain(`urgency position ${slug} --first`);
    fits(hints);
    fits(root.querySelector<HTMLElement>('.kmd-detail')!);
  });
  it.each([320, 390])('offers the enable CLI example for a retired urgency at %ipx', async (width) => {
    const { harness, root } = await openUrgencies(width);
    await TestBed.inject(Router).navigateByUrl('/admin/urgencies/urg_retired');
    for (let i = 0; i < 100 && !root.querySelector('[data-testid="admin-urgency-cli-actions"]')?.textContent?.includes('urgency enable'); i++) {
      harness.fixture.detectChanges();
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    const retiredHints = root.querySelector<HTMLElement>('[data-testid="admin-urgency-cli-actions"]')!;
    expect(retiredHints.textContent).toContain(`urgency enable ${slug}`);
    expect(root.querySelector('[data-testid="admin-urgency-enable"]')).toBeNull();
    fits(retiredHints);
    fits(root.querySelector<HTMLElement>('.kmd-detail')!);
  });
  it('shows desktop list, detail and write controls', async () => {
    const { harness, root } = await openUrgencies(1280);
    expect(root.querySelector('[data-testid="admin-urgencies-new"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="admin-urgencies-default"]')).not.toBeNull();
    expect(root.querySelectorAll('.drag-handle')).toHaveLength(2);
    expect(root.querySelector('[data-testid="admin-urgencies-cli"]')).toBeNull();
    await TestBed.inject(Router).navigateByUrl('/admin/urgencies/urg_test');
    for (let i = 0; i < 10; i++) {
      harness.fixture.detectChanges();
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    expect(root.querySelector('[data-testid="admin-urgency-edit"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="admin-urgency-retire"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="admin-urgency-cli-actions"]')).toBeNull();
    fits(root.querySelector<HTMLElement>('.kmd-list')!);
    fits(root.querySelector<HTMLElement>('.kmd-detail')!);
  });
});
