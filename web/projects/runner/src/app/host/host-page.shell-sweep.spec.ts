import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { settle, stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { HostPage } from './host-page';

/**
 * The host page (`/host`) — its own half of `web:shell-sweep`
 * (`blizzard-context:/verification/blizzard.md` bzh:web-shell-sweep). A host of two
 * runners (one halted, with a long reason; one serving two long-named bindings and holding
 * a chunk), two workspaces whose held environment names its runner and project, and
 * subscriptions naming their spenders. A runner row's facts sit beside its held chunks at a
 * desktop width and genuinely stack under them at phone widths — the
 * `@media (max-width: 900px)` collapse jsdom cannot evaluate — with no horizontal overflow,
 * and every runner row and environment line inside the viewport. Served a registration state
 * this build does not know, the title's registration badge reads the raw state at every width,
 * with no horizontal overflow and no page error.
 *
 * Excluded from the default `ng test runner` run (`angular.json`'s `test.exclude`) because
 * it needs `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const WIDTHS = [1400, 1280, 1024, 768, 390, 320];

const RUNNER_ID = 'rn_01KXKVVF1J3D6H6VYZ3XYN3YJ9';
const CHUNK_ID = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';

const RUNNERS = {
  items: [
    {
      runner_id: RUNNER_ID,
      name: 'r-claude',
      state: 'serving',
      served: true,
      halted_reasons: [],
      pause: { local: false, hub: true, effective: true, local_reason: null },
      agents_in_use: 2,
      max_agents: 2,
      bindings: [
        { project: 'blizzard', workspace_id: 'blizzard-workspace-with-a-long-identifier', agents: 2 },
        { project: 'blizzard-context', workspace_id: 'blizzard-workspace-with-a-long-identifier', agents: 1 },
      ],
    },
    {
      runner_id: null,
      name: 'r-chatgpt',
      state: 'halted',
      served: false,
      bindings: [],
      halted_reasons: ['the hub refused this runner’s token: it was revoked or never minted for this host'],
    },
  ],
};

const WORKSPACES = {
  items: [
    { workspace_id: 'blizzard-workspace-with-a-long-identifier', max_environments: 3 },
    { workspace_id: 'scratch', max_environments: 1 },
  ],
};

const ENVIRONMENTS = {
  workspaces: [
    {
      workspace_id: 'blizzard-workspace-with-a-long-identifier',
      items: [
        {
          environment_id: 'r1',
          held: true,
          chunk_id: CHUNK_ID,
          held_since: '2026-07-16T11:18:00.000Z',
          runner_id: RUNNER_ID,
          project: 'blizzard-context',
        },
        { environment_id: 'r2', held: false },
        { environment_id: 'r3', held: false },
      ],
    },
    { workspace_id: 'scratch', items: [{ environment_id: 's1', held: false }] },
  ],
};

const SUBSCRIPTIONS = {
  items: [
    {
      slug: 'claude-max',
      name: 'Claude Max',
      provider: 'anthropic',
      ok: false,
      miss_reason: 'credential_lapsed',
      sampled_at: '2026-07-16T11:59:26.000Z',
      spenders: [RUNNER_ID],
    },
    { slug: 'chatgpt-pro', name: 'ChatGPT Pro', provider: 'openai', ok: true, sampled_at: '2026-07-16T11:59:26.000Z', spenders: [] },
  ],
};

const REGISTRATION = {
  state: 'registered',
  host_id: 'hs_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
  tenant_id: 'tn_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
  host_name: 'workstation-with-a-long-hostname',
  answered_at: '2026-07-16T11:59:26.000Z',
  held_runner_ids: [RUNNER_ID],
  detail: null,
  buffered_facts: 0,
};

const DASHBOARD = {
  runner: {
    runner_id: RUNNER_ID,
    workspace_id: 'blizzard-workspace-with-a-long-identifier',
    pause: { local: false, hub: true, effective: true, local_reason: null },
    capacities: {
      used: 1,
      bindings: [
        { project: 'blizzard', project_id: 'proj_b', workspace_id: 'blizzard-workspace-with-a-long-identifier', agents: 2, used: 0, free: 2 },
        { project: 'blizzard-context', project_id: 'proj_c', workspace_id: 'blizzard-workspace-with-a-long-identifier', agents: 1, used: 1, free: 0 },
      ],
    },
    hub: { endpoint: 'http://127.0.0.1:8421', reachable: true, last_contact_at: '2026-07-16T11:59:26.000Z', buffer_depth: 0 },
    last_tick_at: '2026-07-16T11:59:26.000Z',
    gates: ['review'],
    served_projects: [
      { entry: 'blizzard', project_id: 'proj_b', slug: 'blizzard', name: 'Blizzard', color: 'amber', resolved: true, answered: true },
      { entry: 'blizzard-context', project_id: 'proj_c', slug: 'blizzard-context', name: 'Blizzard Context', color: 'teal', resolved: true, answered: true },
    ],
  },
  asks: { items: [] },
  escalations: { items: [] },
  takeovers: { items: [] },
  facts: { items: [] },
  harness_health: {
    items: [{ harness_id: 'claude_code', version: '2.1.278', available: true, cause: null, degradations: [], admitted_range: '>=2.1,<3.0' }],
  },
  fleet_summary: null,
};

const LEASES = {
  items: [
    {
      lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
      chunk_id: CHUNK_ID,
      graph_id: 'gr_1',
      node_id: 'nd_build',
      node_name: 'build-with-a-long-node-name',
      epoch: 1,
      session_id: 'sess-1',
      harness_id: 'claude_code',
      pid: 4821,
      environment_id: 'r1',
      workdir: '/ws/r1',
      created_at: '2026-07-16T11:00:00.000Z',
      last_heartbeat_at: '2026-07-16T11:59:26.000Z',
      state: 'running',
      display_status: 'running',
      closed_at: null,
      closure_reason: null,
    },
  ],
};

/** A registration state a newer runner host may report that this build's generated vocabulary does not list. */
const UNKNOWN_STATE = 'suspended_for_review';

function route(method: string, path: string): unknown {
  if (method !== 'GET') return {};
  if (path === '/api/host/runners') return RUNNERS;
  if (path === '/api/host/workspaces') return WORKSPACES;
  if (path === '/api/host/environments') return ENVIRONMENTS;
  if (path === '/api/host/subscriptions') return SUBSCRIPTIONS;
  if (path === '/api/host/registration') return REGISTRATION;
  if (path === `/runners/${RUNNER_ID}/api/dashboard`) return DASHBOARD;
  if (path === `/runners/${RUNNER_ID}/api/leases`) return LEASES;
  return {};
}

describe('runner host page shell sweep (web:shell-sweep)', () => {
  it('lays runner rows and environment lines out inside the viewport at every width, facts beside held chunks only when wide', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);
    const stub = stubRequestClient(runnerClient, route);

    await TestBed.configureTestingModule({
      imports: [HostPage],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(HostPage);
    const root = fixture.nativeElement as HTMLElement;
    root.style.display = 'block';
    root.style.height = '800px';
    document.body.appendChild(root);
    await settle(fixture);

    try {
      for (const width of WIDTHS) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const label = `width=${width}`;
        const vw = window.innerWidth;

        const hostEl = root.querySelector<HTMLElement>('[data-testid="host-page"]')!;
        expect(hostEl.scrollWidth, `${label}: the host page overflows horizontally`).toBeLessThanOrEqual(vw);

        const rows = [...root.querySelectorAll<HTMLElement>('[data-testid="host-runner"]')];
        expect(rows.length, `${label}: not every runner has a row`).toBe(2);
        for (const row of rows) {
          expect(row.getBoundingClientRect().right, `${label}: a runner row is past the viewport`).toBeLessThanOrEqual(vw);
          expect(row.scrollWidth, `${label}: a runner row overflows itself`).toBeLessThanOrEqual(row.clientWidth);
        }

        const serving = rows[0];
        const facts = serving.querySelector<HTMLElement>('.facts')!.getBoundingClientRect();
        const held = serving.querySelector<HTMLElement>('.held')!.getBoundingClientRect();
        if (width > 900) {
          expect(held.left, `${label}: the held chunks do not sit beside the facts`).toBeGreaterThanOrEqual(facts.right);
          expect(held.top, `${label}: the held chunks do not sit beside the facts`).toBe(facts.top);
        } else {
          expect(held.top, `${label}: the held chunks do not stack under the facts`).toBeGreaterThanOrEqual(facts.bottom);
        }
        const chunk = serving.querySelector<HTMLElement>('[data-testid="host-runner-chunk"]')!;
        expect(chunk.getBoundingClientRect().right, `${label}: the held chunk link is past the viewport`).toBeLessThanOrEqual(vw);
        for (const binding of serving.querySelectorAll<HTMLElement>('[data-testid="host-runner-binding"]')) {
          expect(binding.getBoundingClientRect().right, `${label}: a binding line is past the viewport`).toBeLessThanOrEqual(vw);
        }

        const envs = [...root.querySelectorAll<HTMLElement>('[data-testid="host-env-held"], [data-testid="host-env-free"]')];
        expect(envs.length, `${label}: not every environment has a line`).toBe(4);
        for (const env of envs) {
          const box = env.getBoundingClientRect();
          expect(box.right, `${label}: an environment line is past the viewport`).toBeLessThanOrEqual(vw);
          for (const cell of env.children) {
            expect(cell.getBoundingClientRect().right, `${label}: an environment line's cell spills out of it`).toBeLessThanOrEqual(
              box.right + 0.5,
            );
          }
        }
        const heldEnv = root.querySelector<HTMLElement>('[data-testid="host-env-held"]')!;
        expect(heldEnv.querySelector('.holder')?.textContent?.trim()).toBe('r-claude');
        expect(heldEnv.querySelector('.project')?.textContent?.trim()).toBe('Blizzard Context');
      }
    } finally {
      root.remove();
      stub.restore();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });

  it('reads an unknown registration state raw in the title badge at every width, with no overflow or page error', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);
    const stub = stubRequestClient(runnerClient, (method, path) =>
      method === 'GET' && path === '/api/host/registration' ? { ...REGISTRATION, state: UNKNOWN_STATE } : route(method, path),
    );

    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [HostPage],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(HostPage);
    const root = fixture.nativeElement as HTMLElement;
    root.style.display = 'block';
    root.style.height = '800px';
    document.body.appendChild(root);
    await settle(fixture);

    try {
      for (const width of WIDTHS) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const label = `width=${width}`;
        const vw = window.innerWidth;
        const hostEl = root.querySelector<HTMLElement>('[data-testid="host-page"]')!;
        expect(hostEl.scrollWidth, `${label}: the host page overflows horizontally`).toBeLessThanOrEqual(vw);

        const badge = root.querySelector<HTMLElement>('[data-testid="host-registration-state"]');
        expect(badge?.textContent?.trim(), `${label}: the registration badge does not read the raw state`).toBe(UNKNOWN_STATE);
        expect(badge!.getBoundingClientRect().right, `${label}: the registration badge is past the viewport`).toBeLessThanOrEqual(vw);
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
