import { type Type, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { vi } from 'vitest';

import { type MeResponse, hubHostsKey, hubRunnersKey } from 'fleet';
import {
  OPERATOR_ME_RESPONSE,
  type RequestClientStub,
  hostRegistryView,
  runnerRegistryView,
  settle,
  stubError,
  stubHubClient,
} from 'fleet/testing';
import { FleetPage } from './fleet-page';

/** The operator's identity with `runner:retire` — the permission the host verbs need. */
const RETIRE_ME: MeResponse = {
  ...OPERATOR_ME_RESPONSE,
  permissions: [...OPERATOR_ME_RESPONSE.permissions, 'runner:retire' as const],
};

const NOW = new Date().toISOString();
const runner = (id: string, host_id: string | null = null) =>
  runnerRegistryView({ runner_id: id, registered_at: NOW, last_seen_at: NOW, host_id });
const RUNNERS = { runners: [runner('rA', 'h1'), runner('rC')] };
const HOST = hostRegistryView({ host_id: 'h1', host_name: 'build-box', last_seen_at: NOW });
const HOSTS = { hosts: [HOST] };
const REFUSAL = 'runner rA holds chunk ch_live';
const TOKEN = 'bzh_host_secret_token';

type Routes = Parameters<typeof stubHubClient>[0];

function routes(over: Routes = {}, me: MeResponse = RETIRE_ME): Routes {
  return {
    'GET /api/me': () => me,
    'GET /api/runners': () => RUNNERS,
    'GET /api/hosts': () => HOSTS,
    'GET /api/chunks': () => ({ chunks: [], next_cursor: null }),
    'POST /api/hosts/{host_id}/retire': () => ({ host: HOST, released_chunk_ids: [] }),
    'POST /api/hosts/{host_id}/token-revocations': () => ({ host: HOST }),
    'POST /api/hosts/{host_id}/enrollments': () => ({ host_id: 'h1', token: TOKEN }),
    ...over,
  };
}

const click = (el: HTMLElement, selector: string) => el.querySelector<HTMLButtonElement>(selector)?.click();

for (const [name, container, release, hostCard] of [
  ['FleetPage', FleetPage, 'fleet-runner-release', 'fleet-card-h1'],
] as const) {
  describe(`${name} host verbs`, () => {
    let stub: RequestClientStub;

    async function render(route: Routes) {
      stub = stubHubClient(route);
      await TestBed.configureTestingModule({
        imports: [container as Type<unknown>],
        providers: [
          provideZonelessChangeDetection(),
          provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
          provideRouter([{ path: 'fleet', component: FleetPage, children: [{ path: ':id', children: [] }] }]),
        ],
      }).compileComponents();
      // The Fleet tab opens its selection from the URL.
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/fleet/h1');
      const fixture = harness.fixture;
      await settle(fixture);
      const invalidate = vi.spyOn(TestBed.inject(QueryClient), 'invalidateQueries');
      return { fixture, el: fixture.nativeElement as HTMLElement, invalidate };
    }

    afterEach(() => stub.restore());

    function expectRefetch(invalidate: ReturnType<typeof vi.spyOn>) {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: hubHostsKey });
      expect(invalidate).toHaveBeenCalledWith({ queryKey: hubRunnersKey });
    }

    it('offers no Release button on any row, whatever the identity holds', async () => {
      const { el } = await render(routes());

      expect(el.querySelector('[data-testid="host-verbs"]')).not.toBeNull();
      expect(el.querySelector(`[data-testid="${release}"]`)).toBeNull();
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
        routes({
          'POST /api/hosts/{host_id}/retire': (request) =>
            stub.forRoute(request.path, 'POST').length === 1
              ? stubError(409, { detail: REFUSAL })
              : { host: HOST, released_chunk_ids: [] },
        }),
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
      const { el } = await render(routes({}, OPERATOR_ME_RESPONSE));

      expect(el.querySelector(`[data-testid="${hostCard}"]`)).not.toBeNull();
      expect(el.querySelector('[data-testid="host-verbs"]')).toBeNull();
      expect(el.querySelector(`[data-testid="${release}"]`)).toBeNull();
    });
  });
}
