import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { FleetPage } from './fleet-page';

/** A `contributor`'s `/api/me` — every day-to-day operating permission, but not
 * the admin-tier `runner:pause`. */
const CONTRIBUTOR_ME = {
  ...OPERATOR_ME_RESPONSE,
  role: 'contributor',
  permissions: OPERATOR_ME_RESPONSE.permissions.filter((p) => p !== 'runner:pause' && p !== 'graph:edit' && p !== 'user:manage'),
};

const NOW = new Date().toISOString();
const runner = (id: string, over: Partial<Record<string, unknown>> = {}) => ({
  runner_id: id,
  workspace_id: 'ws_a',
  registered_at: NOW,
  last_seen_at: NOW,
  online: true,
  hub_paused: false,
  locally_paused: false,
  ...over,
});
const RUNNERS = {
  runners: [runner('rn_online', { env_capacity: 4 }), runner('rn_paused', { hub_paused: true })],
};
const CHUNKS = [
  {
    chunk_id: 'ch_01claim000000000000000000000',
    graph_id: 'gr_1',
    status: 'running',
    current_node_id: 'nd_build',
    current_node_name: 'build',
    model: 'claude-opus-4-8',
    runner_id: 'rn_online',
    environment_count: 2,
  },
];

/**
 * The mobile Fleet page's container half, exercised through the real
 * `/api/runners` + `/api/chunks` reads rather than through `FleetView`'s
 * inputs directly, since this is the seam that folds them
 * (`injectRunnerRows`) and wires the pause mutation.
 */
describe('FleetPage (mobile Fleet screen)', () => {
  let stub: RequestClientStub;

  async function render() {
    await TestBed.configureTestingModule({
      imports: [FleetPage],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetPage);
    await settle(fixture);
    return fixture;
  }

  afterEach(() => stub.restore());

  it('renders the registry rows the shared runner-rows fold produces, including claims', async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path === '/api/runners') return RUNNERS;
      if (method === 'GET' && path === '/api/chunks') return { chunks: CHUNKS, next_cursor: null };
      return {};
    });
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="mobile-fleet-runner"]')).toHaveLength(2);
    const claims = el.querySelectorAll('[data-runner="rn_online"] [data-testid="mobile-fleet-runner-claim"]');
    expect(claims).toHaveLength(1);
    expect(claims[0].textContent).toContain('build');
    expect(el.querySelector('[data-runner="rn_paused"] [data-testid="mobile-fleet-runner-hub-paused"]')).not.toBeNull();
  });

  it('pauses an online runner via the client call', async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path === '/api/runners') return RUNNERS;
      if (method === 'GET' && path === '/api/chunks') return { chunks: CHUNKS, next_cursor: null };
      if (path === '/api/runners/rn_online/pause') return RUNNERS.runners[0];
      return {};
    });
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-runner="rn_online"] [data-testid="mobile-fleet-runner-toggle"]')?.click();
    await settle(fixture);

    const calls = stub.forRoute('/api/runners/rn_online/pause', 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ by: 'operator' });
  });

  it('withholds the hub brake for a contributor (no runner:pause) — the registry still renders', async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return CONTRIBUTOR_ME;
      if (method === 'GET' && path === '/api/runners') return RUNNERS;
      if (method === 'GET' && path === '/api/chunks') return { chunks: CHUNKS, next_cursor: null };
      return {};
    });
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="mobile-fleet-runner"]')).toHaveLength(2);
    expect(el.querySelectorAll('[data-testid="mobile-fleet-runner-toggle"]')).toHaveLength(0);
  });
});
