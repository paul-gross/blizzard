import { type Type, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { hubClient, hubHostsKey, hubRunnersKey } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';
import { RunnerPanel } from './runner-panel';
import { RunnersPage } from './runners-page';

/** The operator's identity with `runner:retire` — the permission the host verbs need. */
const RETIRE_ME = {
  ...OPERATOR_ME_RESPONSE,
  permissions: [...OPERATOR_ME_RESPONSE.permissions, 'runner:retire' as const],
};

const NOW = new Date().toISOString();
const runner = (id: string, over: Partial<Record<string, unknown>> = {}) => ({
  runner_id: id,
  runner_name: id,
  workspace_id: 'ws_a',
  registered_at: NOW,
  last_seen_at: NOW,
  online: true,
  hub_paused: false,
  locally_paused: false,
  ...over,
});
const RUNNERS = { runners: [runner('rA', { host_id: 'h1' }), runner('rC')] };
const HOST = {
  host_id: 'h1',
  host_name: 'build-box',
  connection: 'online',
  online: true,
  last_seen_at: NOW,
  workspaces: [],
  subscriptions: [],
  subscription_usage: [],
};
const HOSTS = { hosts: [HOST] };
const REFUSAL = 'runner rA holds chunk ch_live';
const TOKEN = 'bzh_host_secret_token';

type Route = (method: string, path: string) => unknown;

function routes(over: Route = () => undefined, me: unknown = RETIRE_ME): Route {
  return (method, path) => {
    const hit = over(method, path);
    if (hit !== undefined) return hit;
    if (method === 'GET' && path === '/api/me') return me;
    if (method === 'GET' && path === '/api/runners') return RUNNERS;
    if (method === 'GET' && path === '/api/hosts') return HOSTS;
    if (method === 'GET' && path === '/api/chunks') return { chunks: [], next_cursor: null };
    if (path === '/api/hosts/h1/releases') return HOST;
    if (path === '/api/hosts/h1/retire') return { host: HOST, released_chunk_ids: [] };
    if (path === '/api/hosts/h1/token-revocations') return { host: HOST };
    if (path === '/api/hosts/h1/enrollments') return { host_id: 'h1', token: TOKEN };
    return {};
  };
}

const click = (el: HTMLElement, selector: string) => el.querySelector<HTMLButtonElement>(selector)?.click();

for (const [name, container, release] of [
  ['RunnerPanel', RunnerPanel, 'runner-release'],
  ['RunnersPage', RunnersPage, 'mobile-fleet-runner-release'],
] as const) {
  describe(`${name} host verbs`, () => {
    let stub: RequestClientStub;

    async function render(route: Route) {
      stub = stubRequestClient(hubClient, route);
      await TestBed.configureTestingModule({
        imports: [container as Type<unknown>],
        providers: [
          provideZonelessChangeDetection(),
          provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        ],
      }).compileComponents();
      const fixture = TestBed.createComponent(container as Type<unknown>);
      await settle(fixture);
      const invalidate = vi.spyOn(TestBed.inject(QueryClient), 'invalidateQueries');
      return { fixture, el: fixture.nativeElement as HTMLElement, invalidate };
    }

    afterEach(() => stub.restore());

    function expectRefetch(invalidate: ReturnType<typeof vi.spyOn>) {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: hubHostsKey });
      expect(invalidate).toHaveBeenCalledWith({ queryKey: hubRunnersKey });
    }

    it('releases a hosted runner after a danger confirm, then re-reads hosts and runners', async () => {
      const { fixture, el, invalidate } = await render(routes());
      expect(el.querySelector(`[data-runner="rC"] [data-testid="${release}"]`)).toBeNull();

      click(el, `[data-runner="rA"] [data-testid="${release}"]`);
      await settle(fixture);
      expect(stub.forRoute('/api/hosts/h1/releases', 'POST')).toHaveLength(0);
      click(el, '[data-testid="host-confirm-confirm"]');
      await settle(fixture);

      const calls = stub.forRoute('/api/hosts/h1/releases', 'POST');
      expect(calls).toHaveLength(1);
      expect(calls[0].body).toEqual({ runner_ids: ['rA'], by: 'operator' });
      expectRefetch(invalidate);
    });

    it('retires the host after a danger confirm, then re-reads hosts and runners', async () => {
      const { fixture, el, invalidate } = await render(routes());

      click(el, '[data-testid="host-retire"]');
      await settle(fixture);
      click(el, '[data-testid="host-confirm-confirm"]');
      await settle(fixture);

      const calls = stub.forRoute('/api/hosts/h1/retire', 'POST');
      expect(calls).toHaveLength(1);
      expect(calls[0].body).toEqual({ by: 'operator', force: false });
      expectRefetch(invalidate);
    });

    it("asks again on the hub's 409, showing its refusal, and forces the retire when confirmed", async () => {
      const { fixture, el } = await render(
        routes((method, path) =>
          path === '/api/hosts/h1/retire' && stub.forRoute(path, 'POST').length === 1
            ? stubError(409, { detail: REFUSAL })
            : undefined,
        ),
      );

      click(el, '[data-testid="host-retire"]');
      await settle(fixture);
      click(el, '[data-testid="host-confirm-confirm"]');
      await settle(fixture);

      expect(el.querySelector('[data-testid="host-force-retire"]')?.textContent).toContain(REFUSAL);
      click(el, '[data-testid="host-force-retire-confirm"]');
      await settle(fixture);

      const calls = stub.forRoute('/api/hosts/h1/retire', 'POST');
      expect(calls.map((call) => call.body)).toEqual([
        { by: 'operator', force: false },
        { by: 'operator', force: true },
      ]);
      expect(el.querySelector('[data-testid="host-force-retire"]')).toBeNull();
    });

    it('revokes the token after a danger confirm, then re-reads hosts and runners', async () => {
      const { fixture, el, invalidate } = await render(routes());

      click(el, '[data-testid="host-revoke-token"]');
      await settle(fixture);
      click(el, '[data-testid="host-confirm-confirm"]');
      await settle(fixture);

      expect(stub.forRoute('/api/hosts/h1/token-revocations', 'POST')).toHaveLength(1);
      expectRefetch(invalidate);
    });

    it('enrolls the host and shows its token once, keeping it in no cache', async () => {
      const { fixture, el, invalidate } = await render(routes());

      click(el, '[data-testid="host-enroll"]');
      await settle(fixture);

      expect(stub.forRoute('/api/hosts/h1/enrollments', 'POST')).toHaveLength(1);
      expectRefetch(invalidate);
      expect(el.querySelector('[data-testid="host-token"]')?.textContent).toBe(TOKEN);
      const queryClient = TestBed.inject(QueryClient);
      expect(JSON.stringify(queryClient.getMutationCache().getAll().map((m) => m.state))).not.toContain(TOKEN);
      expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(TOKEN);

      click(el, '[data-testid="host-token-done"]');
      await settle(fixture);
      expect(el.textContent).not.toContain(TOKEN);
    });

    it('withholds every host verb without runner:retire', async () => {
      expect(OPERATOR_ME_RESPONSE.permissions).not.toContain('runner:retire' as const);
      const { el } = await render(routes(undefined, OPERATOR_ME_RESPONSE));

      expect(el.querySelector('[data-testid="host-card"]')).not.toBeNull();
      expect(el.querySelector('[data-testid="host-verbs"]')).toBeNull();
      expect(el.querySelector(`[data-testid="${release}"]`)).toBeNull();
    });
  });
}
