import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { settle, stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { HostPage } from './host-page';

/**
 * The host page (`/`) — its own half of `web:shell-sweep`
 * (`blizzard-context:/verification/blizzard.md` bzh:web-shell-sweep). A host of two
 * runners (one halted, with a long reason), two workspaces whose held environments name
 * their runner and project, and subscriptions naming their spenders: the three panels
 * sit side by side at a desktop width and genuinely stack at phone widths — the
 * `@media (max-width: 900px)` grid collapse jsdom cannot evaluate — with no horizontal
 * overflow and every runner link on-screen.
 *
 * Excluded from the default `ng test runner` run (`angular.json`'s `test.exclude`) because
 * it needs `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const WIDTHS = [1400, 1280, 1024, 768, 390, 320];

const RUNNERS = {
  items: [
    {
      runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
      name: 'r-claude',
      state: 'serving',
      pause: { local: false, hub: true, effective: true },
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
          chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
          held_since: '2026-07-16T11:18:00.000Z',
          runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
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
      spenders: ['rn_01KXKVVF1J3D6H6VYZ3XYN3YJ9'],
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
  held_runner_ids: ['rn_01KXKVVF1J3D6H6VYZ3XYN3YJ9'],
  detail: null,
  buffered_facts: 0,
};

function route(method: string, path: string): unknown {
  if (method !== 'GET') return {};
  if (path === '/api/host/runners') return RUNNERS;
  if (path === '/api/host/workspaces') return WORKSPACES;
  if (path === '/api/host/environments') return ENVIRONMENTS;
  if (path === '/api/host/subscriptions') return SUBSCRIPTIONS;
  if (path === '/api/host/registration') return REGISTRATION;
  return {};
}

describe('runner host page shell sweep (web:shell-sweep)', () => {
  it('lays the three panels out side by side when wide and stacked when narrow, with no horizontal overflow', async () => {
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
        const panes = ['host-runners-pane', 'host-environments-pane', 'host-subscriptions-pane'].map(
          (testid) => root.querySelector<HTMLElement>(`[data-testid="${testid}"]`)!.getBoundingClientRect(),
        );
        if (width > 900) {
          expect(panes[0].top, `${label}: the panels do not sit side by side`).toBe(panes[1].top);
          expect(panes[1].top, `${label}: the panels do not sit side by side`).toBe(panes[2].top);
        } else {
          expect(panes[1].top, `${label}: environments do not stack under runners`).toBeGreaterThanOrEqual(panes[0].bottom);
          expect(panes[2].top, `${label}: subscriptions do not stack under environments`).toBeGreaterThanOrEqual(panes[1].bottom);
        }
        for (const pane of panes) {
          expect(pane.right, `${label}: a panel's right edge is past the viewport`).toBeLessThanOrEqual(window.innerWidth);
        }

        const hostEl = root.querySelector<HTMLElement>('[data-testid="host-page"]')!;
        expect(hostEl.scrollWidth, `${label}: the host page overflows horizontally`).toBeLessThanOrEqual(window.innerWidth);

        const link = root.querySelector<HTMLElement>('[data-testid="host-runner-link"]')!;
        expect(link.getBoundingClientRect().right, `${label}: the runner link is past the viewport`).toBeLessThanOrEqual(
          window.innerWidth,
        );
        expect(root.querySelector('[data-testid="env-holder"]')?.textContent?.trim()).toBe('r-claude · blizzard-context');
        expect(root.querySelector('[data-testid="subscription-pace-group-spenders"]')?.textContent?.trim()).toBe(
          'spent by r-claude',
        );
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
