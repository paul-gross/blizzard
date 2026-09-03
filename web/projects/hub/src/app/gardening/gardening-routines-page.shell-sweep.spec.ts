import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet, type Routes } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { settle, stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { GardeningRoutineDetail } from './gardening-routine-detail';
import { GardeningRoutinesPage } from './gardening-routines-page';

/**
 * The gardening routines container's own `.gr-layout` two-column split
 * (`gardening-routines-page.css`) — the master/detail grid
 * `routine-panel.shell-sweep.spec.ts` never mounts, since it stands `FleetRoutinePanel`
 * up in isolation. A real, headless-Chromium proof that the `@media (max-width:
 * 720px)` rule collapses the grid to a single stacked column (`chunk-node-history-
 * tab.css`'s own 320px-at-720px master/detail breakpoint): jsdom parses the query
 * without ever evaluating it (`bzh:narrow-viewport-tier-rule`) — gardening sits in
 * the hub's mobile bottom tab bar, so the narrow width is load-bearing, not
 * incidental.
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

/**
 * Stands in for `GardeningPage`'s own shell around the tab — `gardening-page.css`'s
 * `:host` flex column and its `.body` outlet wrapper, reproduced here so the height
 * chain the columns resolve against is the real one. The tab is mounted through the
 * real router because the list and its detail are a parent/child route pair now
 * (`app.routes.ts`); a stubbed `ActivatedRoute` would leave the right column empty
 * and there would be no second column to lay out.
 */
@Component({
  selector: 'app-test-routines-shell-host',
  imports: [RouterOutlet],
  template: '<div class="shell-body"><router-outlet /></div>',
  styles: `
    :host {
      display: flex;
      flex-direction: column;
      flex: 1;
      min-height: 0;
    }
    .shell-body {
      flex: 1;
      min-height: 0;
      display: flex;
      flex-direction: column;
      padding: 8px;
    }
  `,
})
class TestRoutinesShellHost {}

const routes: Routes = [
  {
    path: 'gardening/routines',
    component: GardeningRoutinesPage,
    children: [
      { path: '', component: GardeningRoutineDetail },
      { path: ':routineName', component: GardeningRoutineDetail },
    ],
  },
];

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
    imports: [TestRoutinesShellHost],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      provideRouter(routes),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(TestRoutinesShellHost);
  await TestBed.inject(Router).navigateByUrl('/gardening/routines');
  await settle(fixture, 12);
  return { fixture, stub };
}

describe('gardening routines page layout shell sweep (web:shell-sweep, blizzard#397)', () => {
  it('sits the left column beside the right above 720px, and stacks them at 700px, 390px, and 320px', async () => {
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

      let left = root.querySelector<HTMLElement>('.gr-left');
      let right = root.querySelector<HTMLElement>('.gr-right');
      expect(left, '1280px: no .gr-left in the DOM').not.toBeNull();
      expect(right, '1280px: no .gr-right in the DOM').not.toBeNull();
      expect(left!.getBoundingClientRect().top).toBe(right!.getBoundingClientRect().top);
      expect(
        left!.getBoundingClientRect().right,
        '1280px: left and right do not sit side by side',
      ).toBeLessThanOrEqual(right!.getBoundingClientRect().left);

      for (const width of [700, 390, 320]) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        left = root.querySelector<HTMLElement>('.gr-left');
        right = root.querySelector<HTMLElement>('.gr-right');
        expect(left, `${width}px: no .gr-left in the DOM`).not.toBeNull();
        expect(right, `${width}px: no .gr-right in the DOM`).not.toBeNull();
        expect(
          left!.getBoundingClientRect().top,
          `${width}px: left and right share a top — the grid did not collapse`,
        ).not.toBe(right!.getBoundingClientRect().top);

        const layout = root.querySelector<HTMLElement>('.gr-layout')!;
        expect(
          layout.scrollWidth,
          `${width}px: layout overflows horizontally (${layout.scrollWidth} > ${layout.clientWidth})`,
        ).toBeLessThanOrEqual(layout.clientWidth);

        // Stacked, each column sizes to its own content and `.body` scrolls the
        // whole tab (`gardening-page.css`) — a column must not still be clipped
        // to a bounded box of its own at this width.
        expect(
          left!.scrollHeight,
          `${width}px: .gr-left is still clipped to its own box below the collapse breakpoint`,
        ).toBeLessThanOrEqual(left!.clientHeight + 1);
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

describe('gardening routines page independent-scroll shell sweep (web:shell-sweep)', () => {
  it('scrolls the left column and the right column separately above 720px, without dragging one along with the other', async () => {
    const MANY_ROUTINES = Array.from({ length: 30 }, (_, i) => ({
      ...ROUTINE,
      routine_id: `rtn_${i}`,
      name: `routine-${i}`,
    }));
    const MANY_MEASUREMENTS = Array.from({ length: 30 }, (_, i) => ({
      scope_slug: 'blizzard',
      produced_at: `2026-01-${String((i % 28) + 1).padStart(2, '0')}T00:00:00Z`,
      measurement: `Measurement ${i} of a long-running sweep window.`,
    }));
    const SWEEPS_LONG = { ...SWEEPS, measurements: MANY_MEASUREMENTS };

    const stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/routines') return MANY_ROUTINES;
      if (method === 'GET' && path === '/api/graphs') return [EFFECTIVE_GRAPH_SUMMARY];
      if (method === 'GET' && path === '/api/graphs/gr_1') return GRAPH_DETAIL;
      if (method === 'GET' && path === '/api/routines/rtn_0/sweeps') return SWEEPS_LONG;
      if (method === 'GET' && path === '/api/routines/trend') return TREND;
      if (method === 'GET' && path === '/api/scopes') return SCOPES;
      if (method === 'GET' && path === '/api/me') return ME;
      return {};
    });

    await TestBed.configureTestingModule({
      imports: [TestRoutinesShellHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(routes),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(TestRoutinesShellHost);
    await TestBed.inject(Router).navigateByUrl('/gardening/routines/routine-0');
    await settle(fixture, 12);

    const root = fixture.nativeElement as HTMLElement;
    // A bounded ancestor, standing in for the real app shell's own
    // height:100%/overflow:hidden chain (`app-shell.css`'s `.shell`) that this
    // container mounts under in production — without it, the grid's own row
    // never resolves a definite height to bound and scroll its columns against.
    root.style.cssText = 'display: flex; flex-direction: column; height: 700px; min-height: 0; overflow: hidden;';
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(1280, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const leftHost = root.querySelector<HTMLElement>('.gr-left')!;
      expect(leftHost, 'no .gr-left in the DOM').not.toBeNull();
      // `.gr-left` is `<fleet-kit-panel>` itself, and `bodyScroll` defaults true
      // (`kit-panel.ts`'s own doc comment) — the panel's *own* `.p-body` is the
      // real scroller once `.gr-left`'s `max-height: 100%` bounds the panel,
      // not `.gr-left`'s outer box, which the header row keeps just tall
      // enough for header + clipped body to exactly fill.
      const left = leftHost.querySelector<HTMLElement>(':scope > .p-body') ?? leftHost;
      // `.gr-right` wraps the routed detail child, which is `display: contents`
      // (`gardening-detail-host.css`) so `fleet-routine-panel` and its three
      // stacked `<fleet-kit-panel>`s (Routine, Activity, Strategy) sit in this
      // column directly. Unlike the
      // left column, those panels do *not* shrink to share the column's bound:
      // `routine-panel.css` gives them `flex: none` so each stands at its own
      // content height, and each passes `bodyScroll` false so no `.p-body`
      // opens a scrollport of its own. The column itself is therefore the one
      // real scroller on this side — a long Activity or Strategy panel is
      // reached by scrolling `.gr-right` past the panels above it.
      const right = root.querySelector<HTMLElement>('.gr-right')!;
      expect(right, 'no .gr-right in the DOM').not.toBeNull();
      const rightHost = right;

      // The other half of that contract: a panel body that opened its own
      // scrollport would take the drag away from the column above.
      for (const body of right.querySelectorAll<HTMLElement>('.p-body')) {
        expect(
          getComputedStyle(body).overflowY,
          'a panel body opened its own scrollport, stealing the drag from the column',
        ).not.toBe('auto');
      }

      expect(
        rightHost.getBoundingClientRect().bottom,
        `the right column grows past its own bounded box (bottom ${rightHost.getBoundingClientRect().bottom}px)`,
      ).toBeLessThanOrEqual(root.getBoundingClientRect().bottom + 1);
      expect(
        left.scrollHeight,
        `the 30-routine list never overflows its own box (${left.scrollHeight} <= ${left.clientHeight})`,
      ).toBeGreaterThan(left.clientHeight);
      expect(
        right.scrollHeight,
        `the right column never overflows its own box (${right.scrollHeight} <= ${right.clientHeight})`,
      ).toBeGreaterThan(right.clientHeight);

      left.scrollTop = left.scrollHeight;
      const leftScrollTop = left.scrollTop;
      expect(leftScrollTop, 'the left column is clipped, not scrollable').toBeGreaterThan(0);
      expect(right.scrollTop, 'scrolling the left column moved the right column along with it').toBe(0);

      right.scrollTop = right.scrollHeight;
      expect(right.scrollTop, 'the right column is clipped, not scrollable').toBeGreaterThan(0);
      expect(
        left.scrollTop,
        'scrolling the right column moved the left column back off where the operator left it',
      ).toBe(leftScrollTop);
    } finally {
      root.remove();
      stub.restore();
    }
  });
});
