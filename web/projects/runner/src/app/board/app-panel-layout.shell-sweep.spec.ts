import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { settle, stubRequestClient } from 'fleet/testing';
import { commands, page } from 'vitest/browser';

import { machineChunkRows } from '../core/chunk-rows.model';
import type { PanelLease } from '../core/panel-reads.model';
import { LocalPanelLayout } from './app-panel-layout';

/**
 * The desktop board's half of `web:shell-sweep` (`bzh:visual-change-needs-a-render`): the
 * chunk lanes and the right rail laid out side by side in a real headless Chromium, which
 * jsdom cannot judge — the three lanes beside one another in the center column, the rail's
 * hub, host and open-asks panels stacked in its own fixed column to their right, and nothing
 * — a long runner name, a two-workspace runner's binding lines, a long-named environment —
 * pushing the page sideways. The same again with a chunk open in the dock below the lanes.
 *
 * Excluded from the default `ng test` run the same way every other `*.shell-sweep.spec.ts`
 * is — run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const WIDTHS = [1280, 1024];

const RUNNER_NAME = 'r-claude-with-a-long-host-name';

const LEASE = (overrides: Partial<PanelLease> = {}): PanelLease => ({
  lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
  chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
  graph_id: 'gr_1',
  node_id: 'nd_build',
  node_name: 'build',
  epoch: 2,
  session_id: 'sess-77',
  pid: 4821,
  environment_id: 'an-environment-with-a-long-name-e12',
  workdir: '/ws/e12',
  created_at: '2026-07-16T11:00:00.000Z',
  last_heartbeat_at: '2026-07-16T11:59:26.000Z',
  state: 'running',
  display_status: 'running',
  closure_healthy: false,
  closed_at: null,
  closure_reason: null,
  stale_after_seconds: 3600,
  owner: { runnerId: 'rn_1', name: RUNNER_NAME, label: RUNNER_NAME },
  projectSlug: 'blizzard',
  ...overrides,
});

/** One chunk per lane, tinted by its project. */
const LEASES = [
  LEASE({ chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYNASK1', lease_id: 'l_ask', state: 'parked', display_status: 'waiting-ask' }),
  LEASE(),
  LEASE({
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYNDONE',
    lease_id: 'l_done',
    state: 'closed',
    display_status: 'closed',
    closure_healthy: true,
    closure_reason: 'transitioned',
    closed_at: '2026-07-16T11:30:00.000Z',
  }),
];
const ROWS = machineChunkRows(LEASES, [{ slug: 'blizzard', name: 'Blizzard', entries: ['blizzard'], color: 'cyan' }]);

/** The host as the live instance reports it: one runner binding two workspaces, one held by its own brake. */
const HOST_RUNNERS = {
  items: [
    {
      runner_id: 'rn_1',
      name: RUNNER_NAME,
      state: 'serving',
      served: true,
      halted_reasons: [],
      pause: { local: false, hub: true, effective: true, local_reason: null },
      bindings: [
        { project: 'console', workspace_id: 'workspace-web', agents: 4 },
        { project: 'blizzard', workspace_id: 'workspace-local', agents: 8 },
      ],
      agents_in_use: 3,
      max_agents: 12,
    },
    {
      runner_id: 'rn_2',
      name: 'r-opencode',
      state: 'serving',
      served: true,
      halted_reasons: [],
      pause: { local: true, hub: false, effective: true, local_reason: 'OpenCode is not installed on this host yet' },
      bindings: [{ project: 'winter', workspace_id: 'workspace-local', agents: 2 }],
      agents_in_use: 0,
      max_agents: 2,
    },
  ],
};

function dashboard(runnerId: string): unknown {
  return {
    fleet_summary: { ready: 12, running: 9, waiting: 3, needs: 2 },
    runner: {
      runner_id: runnerId,
      workspace_id: 'workspace-local',
      pause: { local: false, hub: false, effective: false, local_reason: null },
      capacities: {
        used: 2,
        bindings: [{ project: 'blizzard', project_id: 'proj_b', workspace_id: 'workspace-local', agents: 8, used: 2, free: 6 }],
      },
      hub: { endpoint: 'http://127.0.0.1:4522', reachable: true, last_contact_at: '2026-07-16T11:59:30.000Z', buffer_depth: 0 },
      last_tick_at: null,
      served_projects: [{ entry: 'blizzard', project_id: 'proj_b', slug: 'blizzard', name: 'Blizzard', color: 'cyan' }],
    },
    asks: {
      items: [
        {
          question_id: 'qn_01KXKVVF1J3D6H6VYZ3XYNQ777',
          chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYNASK1',
          lease_id: 'l_ask',
          question: 'Should the probe cap be configurable per service, or stay one value for every service on the host?',
          options: [],
          session_id: 'sess-77',
          asked_at: '2026-07-16T11:40:00.000Z',
        },
      ],
    },
    escalations: { items: [] },
    takeovers: { items: [] },
    facts: { items: [] },
    harness_health: { items: [] },
  };
}

const ENVIRONMENTS = {
  workspaces: [
    {
      workspace_id: 'workspace-local',
      items: Array.from({ length: 12 }, (_, i) => ({
        environment_id: `e${i + 1}`,
        chunk_id: i < 3 ? `ch_${i}` : null,
        held_since: i < 3 ? '2026-07-16T11:00:00.000Z' : null,
        held: i < 3,
        runner_id: i < 3 ? 'rn_1' : null,
        project: i < 3 ? 'proj_b' : null,
      })),
    },
  ],
};

function routes(method: string, path: string): unknown {
  if (method !== 'GET') return {};
  if (path === '/api/host/runners') return HOST_RUNNERS;
  if (path === '/api/host/environments') return ENVIRONMENTS;
  if (path === '/api/host/workspaces') return { items: [{ workspace_id: 'workspace-local', max_environments: 12 }] };
  if (path === '/api/host/subscriptions') return { items: [] };
  const match = /^\/runners\/([^/]+)\/api\/dashboard$/.exec(path);
  if (match) return dashboard(match[1]);
  return { items: [] };
}

/** The design tokens are a global stylesheet the app build loads, never a standalone mount. */
async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
}

async function render(width: number, selected: boolean) {
  TestBed.resetTestingModule();
  const stub = stubRequestClient(runnerClient, routes);
  await TestBed.configureTestingModule({
    imports: [LocalPanelLayout],
    providers: [
      provideZonelessChangeDetection(),
      provideRouter([]),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalPanelLayout);
  const inputs = {
    chunksTriadState: 'ready',
    machineChunks: ROWS,
    openAskCount: 1,
    selectedChunkId: selected ? LEASE().chunk_id : null,
    selectedChunkLeases: selected ? [LEASE()] : [],
    selectedStatus: selected ? ROWS[1].status : null,
    selectedEscalation: null,
  };
  for (const [key, value] of Object.entries(inputs)) fixture.componentRef.setInput(key, value);
  // The panel fills the app's content area; a fixed-height frame stands in for it.
  const frame = document.createElement('div');
  frame.style.height = '800px';
  frame.appendChild(fixture.nativeElement as HTMLElement);
  document.body.appendChild(frame);
  await page.viewport(width, 800);
  await settle(fixture);
  await new Promise((resolve) => requestAnimationFrame(resolve));
  return { root: fixture.nativeElement as HTMLElement, frame, stub };
}

const rectOf = (root: ParentNode, selector: string): DOMRect => {
  const el = root.querySelector<HTMLElement>(selector);
  expect(el, `no ${selector} in the DOM`).not.toBeNull();
  return el!.getBoundingClientRect();
};

describe('runner desktop board shell sweep (web:shell-sweep)', () => {
  beforeAll(loadDesignTokens);

  for (const width of WIDTHS) {
    for (const selected of [false, true]) {
      const label = `width=${width}${selected ? ', dock open' : ''}`;

      it(`lays the lanes and the rail side by side with no horizontal overflow at ${label}`, async () => {
        const { root, frame, stub } = await render(width, selected);
        try {
          expect(
            document.documentElement.scrollWidth,
            `${label}: the page overflows horizontally (${document.documentElement.scrollWidth} > ${window.innerWidth})`,
          ).toBeLessThanOrEqual(window.innerWidth);

          // The rail sits in its own column to the right of the board, both starting on one line.
          const center = rectOf(root, '.col.center');
          const right = rectOf(root, '.col.right');
          expect(right.left, `${label}: the rail is not to the right of the board`).toBeGreaterThanOrEqual(center.right - 1);
          expect(Math.abs(right.top - center.top), `${label}: the board and the rail do not share a top`).toBeLessThanOrEqual(1);
          expect(right.right, `${label}: the rail extends past the viewport`).toBeLessThanOrEqual(window.innerWidth);

          // The three lanes sit beside one another, left to right.
          const lanes = ['needs', 'progress', 'done'].map((lane) => rectOf(root, `[data-testid="lane-${lane}"]`));
          for (let i = 1; i < lanes.length; i++) {
            expect(lanes[i].left, `${label}: lane ${i} is not to the right of lane ${i - 1}`).toBeGreaterThanOrEqual(lanes[i - 1].right - 1);
            expect(Math.abs(lanes[i].top - lanes[0].top), `${label}: the lanes do not share a top`).toBeLessThanOrEqual(1);
          }
          expect(lanes[2].right, `${label}: the lanes overflow the board column`).toBeLessThanOrEqual(center.right + 1);

          // Every card stays inside its own box — its long-named runner and environment included.
          for (const card of Array.from(root.querySelectorAll<HTMLElement>('[data-testid="chunk-row"]'))) {
            expect(card.scrollWidth, `${label}: card ${card.dataset['chunkId']} overflows (${card.scrollWidth} > ${card.clientWidth})`).toBeLessThanOrEqual(
              card.clientWidth,
            );
          }

          // The rail's panels stack hub → host → open asks, each inside the rail's column.
          const panes = ['rail-hub-pane', 'rail-host-pane', 'rail-asks-pane'].map((testid) => rectOf(root, `[data-testid="${testid}"]`));
          for (let i = 1; i < panes.length; i++) {
            expect(panes[i].top, `${label}: rail panel ${i} is not below panel ${i - 1}`).toBeGreaterThanOrEqual(panes[i - 1].bottom - 1);
          }
          for (const pane of panes) {
            expect(pane.left).toBeGreaterThanOrEqual(right.left - 1);
            expect(pane.right).toBeLessThanOrEqual(right.right + 1);
          }
          expect(root.querySelectorAll('[data-testid="host-panel-runner"]')).toHaveLength(2);
          expect(root.querySelectorAll('[data-testid="host-panel-runner-binding"]').length).toBeGreaterThan(0);
          for (const runner of Array.from(root.querySelectorAll<HTMLElement>('[data-testid="host-panel-runner"]'))) {
            expect(runner.scrollWidth, `${label}: a host-panel runner row overflows`).toBeLessThanOrEqual(runner.clientWidth);
          }

          if (selected) {
            // The dock opens below the lanes, inside the board column.
            const dock = rectOf(root, 'app-machine-detail');
            const board = rectOf(root, '[data-testid="board"]');
            expect(dock.height, `${label}: the dock takes no room`).toBeGreaterThan(0);
            expect(dock.top, `${label}: the dock is not below the lanes`).toBeGreaterThanOrEqual(board.bottom - 1);
            expect(dock.right, `${label}: the dock overflows the board column`).toBeLessThanOrEqual(center.right + 1);
          }
        } finally {
          frame.remove();
          stub.restore();
        }
      });
    }
  }
});
