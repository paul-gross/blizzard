import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { settle, stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { GardeningRoutinesPage } from './gardening-routines-page';

/**
 * The gardening routines container's own `.gr-layout` two-column split
 * (`gardening-routines-page.css`) — the list-beside-panel grid
 * `routine-panel.shell-sweep.spec.ts` never mounts, since it stands `FleetRoutinePanel`
 * up in isolation. A real, headless-Chromium proof that the `@media (max-width: 480px)`
 * rule collapses the grid to a single stacked column: jsdom parses the query without
 * ever evaluating it (`bzh:narrow-viewport-tier-rule`) — gardening sits in the hub's
 * mobile bottom tab bar, so the narrow width is load-bearing, not incidental.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`) because
 * it needs `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const ROUTINE = {
  routine_id: 'rtn_1',
  name: 'nightly',
  graph_name: 'garden-routine',
  default_scope_slug: 'blizzard',
  default_model: ['claude-sonnet-5'],
  default_effort: 'medium',
  created_at: '2026-01-01T00:00:00Z',
};

const EFFECTIVE_GRAPH_SUMMARY = {
  graph_id: 'gr_1',
  name: 'garden-routine',
  entry_node_id: 'nd_1',
  created_at: '2026-01-01T00:00:00Z',
  effective: true,
};

const GRAPH_DETAIL = {
  graph_id: 'gr_1',
  name: 'garden-routine',
  entry_node_id: 'nd_1',
  enabled: true,
  nodes: [{ node_id: 'nd_1', name: 'survey', executor: 'claude', judged_by: 'none', prompt: 'Survey the repo.' }],
};

const SWEEPS = {
  routine_name: 'nightly',
  since: '2026-01-01T00:00:00Z',
  until: '2026-01-29T00:00:00Z',
  last_swept: [
    { scope_slug: 'blizzard', finding_set_id: 'fins_1', produced_at: '2026-01-10T00:00:00Z', revisions: { blizzard: 'abc123' } },
  ],
  measurements: [{ scope_slug: 'blizzard', produced_at: '2026-01-10T00:00:00Z', measurement: '3 findings' }],
};

const SCOPES = [
  { slug: 'blizzard', description: 'the hub, runner, CLI and board', created_at: '2026-01-01T00:00:00Z', retired: false },
];

const ME = {
  user_id: 'usr_1',
  username: 'gardener',
  display_name: 'Gardener',
  role: 'admin',
  permissions: ['graph:edit'],
};

const TREND = {
  routine_name: 'nightly',
  since: '2026-01-01T00:00:00Z',
  until: '2026-01-29T00:00:00Z',
  period_days: 7,
  periods: [
    { period_start: '2026-01-01T00:00:00Z', period_end: '2026-01-08T00:00:00Z', created: 2, exits: {}, outflow: 1, withdrawn: 0, reopened: 0 },
  ],
  age: { boundary: '2026-01-01T00:00:00Z', recent: 2, older: 0, unattributed: 0 },
};

async function render() {
  const stub = stubRequestClient(hubClient, (method, path) => {
    if (method === 'GET' && path === '/api/routines') return [ROUTINE];
    if (method === 'GET' && path === '/api/graphs') return [EFFECTIVE_GRAPH_SUMMARY];
    if (method === 'GET' && path === '/api/graphs/gr_1') return GRAPH_DETAIL;
    if (method === 'GET' && path === '/api/routines/rtn_1/sweeps') return SWEEPS;
    if (method === 'GET' && path === '/api/routines/trend') return TREND;
    if (method === 'GET' && path === '/api/scopes') return SCOPES;
    if (method === 'GET' && path === '/api/me') return ME;
    return {};
  });
  await TestBed.configureTestingModule({
    imports: [GardeningRoutinesPage],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(GardeningRoutinesPage);
  await settle(fixture, 12);
  return { fixture, stub };
}

describe('gardening routines page layout shell sweep (web:shell-sweep, blizzard#397)', () => {
  it('sits the list beside the panel above 480px, and stacks them at 390px and 320px', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    const { fixture, stub } = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(1280, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      let list = root.querySelector<HTMLElement>('.gr-list');
      let panel = root.querySelector<HTMLElement>('.gr-panel');
      expect(list, '1280px: no .gr-list in the DOM').not.toBeNull();
      expect(panel, '1280px: no .gr-panel in the DOM').not.toBeNull();
      expect(list!.getBoundingClientRect().top).toBe(panel!.getBoundingClientRect().top);
      expect(
        list!.getBoundingClientRect().right,
        '1280px: list and panel do not sit side by side',
      ).toBeLessThanOrEqual(panel!.getBoundingClientRect().left);

      for (const width of [390, 320]) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        list = root.querySelector<HTMLElement>('.gr-list');
        panel = root.querySelector<HTMLElement>('.gr-panel');
        expect(list, `${width}px: no .gr-list in the DOM`).not.toBeNull();
        expect(panel, `${width}px: no .gr-panel in the DOM`).not.toBeNull();
        expect(
          list!.getBoundingClientRect().top,
          `${width}px: list and panel share a top — the grid did not collapse`,
        ).not.toBe(panel!.getBoundingClientRect().top);

        const layout = root.querySelector<HTMLElement>('.gr-layout')!;
        expect(
          layout.scrollWidth,
          `${width}px: layout overflows horizontally (${layout.scrollWidth} > ${layout.clientWidth})`,
        ).toBeLessThanOrEqual(layout.clientWidth);
      }
    } finally {
      root.remove();
      stub.restore();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});
