import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';
import { hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { RunnersPage } from './runners-page';

/** An admin's `/api/me` — `runner:retire` too, so a host verb would render anywhere it is offered. */
const ADMIN_ME = {
  ...OPERATOR_ME_RESPONSE,
  permissions: [...OPERATOR_ME_RESPONSE.permissions, 'runner:retire' as const],
};
/** A `contributor`'s `/api/me` — every day-to-day operating permission, but not
 * the admin-tier `runner:pause`. */
const CONTRIBUTOR_ME = {
  ...OPERATOR_ME_RESPONSE,
  role: 'contributor',
  permissions: OPERATOR_ME_RESPONSE.permissions.filter((p) => p !== 'runner:pause' && p !== 'graph:edit' && p !== 'user:manage'),
};

const NOW = new Date().toISOString();
const runner = (id: string, over: Record<string, unknown> = {}) => ({
  runner_id: id,
  runner_name: id,
  workspace_id: 'ws_a',
  registered_at: NOW,
  last_seen_at: NOW,
  online: true,
  connection: 'online',
  hub_paused: false,
  locally_paused: false,
  capabilities: [],
  gates: [],
  subscription_unrostered: false,
  ...over,
});
const RUNNERS = {
  runners: [runner('rn_online', { host_id: 'hs_h1', env_capacity: 4 }), runner('rn_paused', { hub_paused: true })],
};
const HOST = {
  host_id: 'hs_h1',
  host_name: 'build-box',
  connection: 'online',
  online: true,
  enrolled: true,
  added_at: NOW,
  last_seen_at: NOW,
  workspaces: [],
  subscriptions: [],
  subscription_usage: [],
};
const CHUNKS = [
  {
    chunk_id: 'ch_01claim000000000000000000000',
    graph_id: 'gr_1',
    status: 'running',
    current_node_id: 'nd_build',
    current_node_name: 'build',
    runner_id: 'rn_online',
    environment_count: 2,
  },
];

/**
 * The mobile Fleet tab's container, driven through the router and the real `/api/runners`,
 * `/api/hosts` and `/api/chunks` reads: a bare `/fleet` is the overview, `/fleet/:id` the opened
 * host's or hostless runner's breakdown.
 */
describe('RunnersPage (mobile Fleet screen)', () => {
  let stub: RequestClientStub;

  async function open(url: string, me: unknown = OPERATOR_ME_RESPONSE, over: (method: string, path: string) => unknown = () => undefined) {
    stub = stubRequestClient(hubClient, (method, path) => {
      const hit = over(method, path);
      if (hit !== undefined) return hit;
      if (path === '/api/me') return me;
      if (path === '/api/runners') return RUNNERS;
      if (path === '/api/hosts') return { hosts: [HOST] };
      if (path === '/api/chunks') return { chunks: CHUNKS, next_cursor: null };
      return {};
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'fleet', component: RunnersPage, children: [{ path: ':id', children: [] }] }]),
      ],
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    return { harness, el: harness.fixture.nativeElement as HTMLElement };
  }

  const toggle = (el: HTMLElement, id: string): HTMLButtonElement | null =>
    el.querySelector<HTMLButtonElement>(`[data-runner="${id}"] [data-testid="runner-toggle"]`);

  afterEach(() => stub.restore());

  describe('the overview (/fleet)', () => {
    it("renders the board rail's runner panel, each runner's name opening its Fleet detail", async () => {
      const { el } = await open('/fleet');

      expect(el.querySelectorAll('[data-testid="runner"]')).toHaveLength(2);
      expect(el.querySelector('[data-runner="rn_online"] [data-testid="runner-fleet-link"]')?.getAttribute('href')).toBe('/fleet/hs_h1');
      expect(el.querySelector('[data-runner="rn_paused"] [data-testid="runner-fleet-link"]')?.getAttribute('href')).toBe('/fleet/rn_paused');
      expect(el.querySelector('[data-testid="mobile-fleet-detail"]')).toBeNull();
    });

    it('labels each toggle by its runner\'s hub brake, and pauses through the client call', async () => {
      const { harness, el } = await open('/fleet', OPERATOR_ME_RESPONSE, (_method, path) =>
        path === '/api/runners/rn_online/pause' ? RUNNERS.runners[0] : undefined,
      );

      expect(toggle(el, 'rn_online')?.textContent?.trim()).toBe('Pause');
      expect(toggle(el, 'rn_paused')?.textContent?.trim()).toBe('Resume');

      toggle(el, 'rn_online')?.click();
      await settle(harness.fixture);

      const calls = stub.forRoute('/api/runners/rn_online/pause', 'POST');
      expect(calls).toHaveLength(1);
      expect(calls[0].body).toEqual({ by: 'operator' });
    });

    it('withholds the toggles for a contributor (no runner:pause) — the runners still render', async () => {
      const { el } = await open('/fleet', CONTRIBUTOR_ME);

      expect(el.querySelectorAll('[data-testid="runner"]')).toHaveLength(2);
      expect(el.querySelectorAll('[data-testid="runner-toggle"]')).toHaveLength(0);
    });

    it("disables only the tapped runner's toggle while its mutation is pending, re-enabling once it settles", async () => {
      const { harness, el } = await open('/fleet', OPERATOR_ME_RESPONSE, (_method, path) =>
        path === '/api/runners/rn_online/pause' ? RUNNERS.runners[0] : undefined,
      );
      const queryClient = TestBed.inject(QueryClient);
      let resolveInvalidate!: () => void;
      vi.spyOn(queryClient, 'invalidateQueries').mockReturnValue(new Promise<void>((resolve) => (resolveInvalidate = resolve)));

      toggle(el, 'rn_online')?.click();
      // Held open by the `invalidateQueries` spy above — a bare macrotask tick and a manual
      // `detectChanges()` stand in for `settle()`'s own `whenStable()`, which would hang on it.
      await new Promise((resolve) => setTimeout(resolve, 0));
      harness.fixture.detectChanges();

      expect(toggle(el, 'rn_online')?.disabled).toBe(true);
      expect(toggle(el, 'rn_paused')?.disabled).toBe(false);

      resolveInvalidate();
      await settle(harness.fixture);

      expect(toggle(el, 'rn_online')?.disabled).toBe(false);
    });

    it('reports a pause failure in a visible notice, without disabling a sibling runner', async () => {
      const { harness, el } = await open('/fleet', OPERATOR_ME_RESPONSE, (_method, path) =>
        path === '/api/runners/rn_online/pause' ? stubError(409, { detail: 'runner already paused' }) : undefined,
      );

      toggle(el, 'rn_online')?.click();
      await settle(harness.fixture);

      expect(el.querySelector('[data-testid="runner-action-error"]')?.textContent).toBe('runner already paused');
      expect(toggle(el, 'rn_paused')?.disabled).toBe(false);
    });
  });

  describe('the detail (/fleet/:id)', () => {
    it('opens a host behind a back bar to the overview, its held chunks each linking to their chunk page', async () => {
      const { el } = await open('/fleet/hs_h1');

      expect(el.querySelector('[data-testid="mobile-fleet-back"]')?.getAttribute('href')).toBe('/fleet');
      expect(el.querySelector('[data-testid="fleet-detail-name"]')?.textContent).toContain('build-box');
      expect(el.querySelector('[data-testid="runner-panel"]')).toBeNull();
      const claim = el.querySelector('[data-runner="rn_online"] [data-testid="fleet-runner-claim-link"]');
      expect(claim?.getAttribute('href')).toBe('/board/chunk/ch_01claim000000000000000000000');
      expect(claim?.textContent).toContain('build');
    });

    it('leaves the host verbs — enroll, revoke, retire — to the desktop Fleet tab, even for an admin', async () => {
      const { el } = await open('/fleet/hs_h1', ADMIN_ME);

      expect(el.querySelector('[data-testid="host-verbs"]')).toBeNull();
    });

    it('opens a hostless runner by its own id', async () => {
      const { el } = await open('/fleet/rn_paused');

      expect(el.querySelector('[data-testid="fleet-detail-name"]')?.textContent).toContain('rn_paused');
    });

    it('names an id that matches no host or runner', async () => {
      const { el } = await open('/fleet/hs_gone');

      expect(el.querySelector('[data-testid="mobile-fleet-not-found"]')?.textContent).toContain('hs_gone');
    });
  });
});
