import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import type { InAppLink } from 'fleet/shell';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { ProjectLens, lensLink } from '../../core/project-lens';
import { ProjectDetail } from './project-detail';

/**
 * The project overview's real-render proof (`web:shell-sweep`) of what it shows before and
 * after its reads answer: no project picked, the queue and revisions while their reads are
 * in flight (no `0`, no empty copy), and a project with no revisions — each without
 * horizontal overflow. Run via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const PROJECT = {
  project_id: 'proj_b',
  slug: 'blizzard',
  name: 'Blizzard',
  description: 'The orchestration platform itself',
  color: 'teal',
  revision: 1,
  created_at: '2026-09-20T00:00:00Z',
};

function lensOnAll() {
  return {
    slug: signal<string | null>(null).asReadonly(),
    lensed: signal(false).asReadonly(),
    link: (link: InAppLink) => lensLink(link, null),
  };
}

describe('ProjectDetail overview shell sweep', () => {
  let stub: RequestClientStub;

  /** Mounts the detail at `url`; `answered` false holds the counts and history reads in flight. */
  async function open(width: number, url: string, answered: boolean) {
    await page.viewport(width, 800);
    const pending = new Promise<unknown>(() => undefined);
    stub = stubRequestClient(hubClient, (_method, path) => {
      if (path === '/api/me') return { ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit'] };
      if (path === '/api/projects/proj_b') return PROJECT;
      if (path.endsWith('/source-links')) return { source_links: [] };
      if (path.endsWith('/repository-links')) return { repository_links: [] };
      if (path === '/api/runners') return { runners: [] };
      if (path === '/api/config/changes') return answered ? { changes: [], total: 0 } : pending;
      if (path === '/api/chunk-counts') return answered ? { ready: 0, running: 0, done: 0, done_since: 0 } : pending;
      return {};
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([
          { path: 'admin/projects', component: ProjectDetail },
          { path: 'admin/projects/:key', component: ProjectDetail },
        ]),
        { provide: ProjectLens, useValue: lensOnAll() },
      ],
    });
    const harness = await RouterTestingHarness.create();
    const root = harness.fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    if (answered) {
      await harness.navigateByUrl(url);
      await settle(harness.fixture);
    } else {
      // A read held in flight keeps the fixture from ever going stable, so wait for the page itself instead.
      void harness.navigateByUrl(url);
      await expect.poll(() => root.querySelector('[data-testid="admin-projects-detail"]'), { timeout: 5000 }).not.toBeNull();
      await expect.poll(() => root.textContent, { timeout: 5000 }).toContain('Loading revisions…');
    }
    return { harness, root };
  }

  afterEach(async () => {
    stub?.restore();
    await page.viewport(1280, 800);
  });

  const find = (root: HTMLElement, testid: string) => root.querySelector<HTMLElement>(`[data-testid="${testid}"]`);

  function noHorizontalOverflow(root: HTMLElement, width: number) {
    expect(root.scrollWidth).toBeLessThanOrEqual(width);
  }

  for (const width of [1280, 390]) {
    it(`asks for a pick when no project is open at ${width}px`, async () => {
      const { harness, root } = await open(width, '/admin/projects', true);
      expect(find(root, 'admin-projects-detail-empty')?.textContent).toContain('Pick a project');
      expect(find(root, 'admin-projects-detail')).toBeNull();
      noHorizontalOverflow(root, width);
      harness.fixture.destroy();
      root.remove();
    });

    it(`shows no queue and no empty revisions while their reads are in flight at ${width}px`, async () => {
      const { harness, root } = await open(width, '/admin/projects/proj_b', false);
      expect(find(root, 'admin-projects-detail')).not.toBeNull();
      expect(find(root, 'admin-projects-queue')).toBeNull();
      expect(find(root, 'admin-projects-revisions-empty')).toBeNull();
      expect(root.textContent).toContain('Loading revisions…');
      expect(root.textContent).not.toContain('0 done today');
      noHorizontalOverflow(root, width);
      harness.fixture.destroy();
      root.remove();
    });

    it(`states the color beside the name and in its fact, and wraps the picker's twelve chips inside the dialog at ${width}px`, async () => {
      const { harness, root } = await open(width, '/admin/projects/proj_b', true);
      expect(find(root, 'admin-projects-color')?.textContent).toContain('teal');
      const header = root.querySelector<HTMLElement>('.name-swatch')!.getBoundingClientRect();
      const name = root.querySelector<HTMLElement>('.name')!.getBoundingClientRect();
      expect(header.right, 'the swatch overlaps the name').toBeLessThanOrEqual(name.left + 0.5);

      // Writes are a desktop affair: the Color control is absent on mobile.
      if (width < 768) {
        expect(find(root, 'admin-projects-color-open')).toBeNull();
        harness.fixture.destroy();
        root.remove();
        return;
      }
      find(root, 'admin-projects-color-open')!.click();
      await settle(harness.fixture);
      const dialog = document.querySelector<HTMLElement>('[data-testid="admin-projects-color-dialog"]')!;
      const chips = [...dialog.querySelectorAll<HTMLElement>('button[data-testid^="project-color-"]')];
      expect(chips).toHaveLength(12);
      const panel = dialog.getBoundingClientRect();
      for (const chip of chips) {
        const box = chip.getBoundingClientRect();
        expect(box.right, 'a chip overflows the dialog').toBeLessThanOrEqual(panel.right + 0.5);
        expect(box.left, 'a chip starts left of the dialog').toBeGreaterThanOrEqual(panel.left - 0.5);
      }
      harness.fixture.destroy();
      root.remove();
    });

    it(`says so when the project has no revisions, once read, at ${width}px`, async () => {
      const { harness, root } = await open(width, '/admin/projects/proj_b', true);
      const empty = find(root, 'admin-projects-revisions-empty')!;
      expect(empty.textContent).toContain('No revisions recorded.');
      expect(empty.getBoundingClientRect().height).toBeGreaterThan(0);
      expect(find(root, 'admin-projects-queue')?.textContent).toContain('0 ready · 0 running · 0 done today');
      expect(find(root, 'admin-projects-created')?.textContent).toMatch(/ago/);
      const tabs = root.querySelector<HTMLElement>('[role="tablist"]')!;
      expect(getComputedStyle(tabs).height).toBe('30px');
      noHorizontalOverflow(root, width);
      harness.fixture.destroy();
      root.remove();
    });
  }
});
