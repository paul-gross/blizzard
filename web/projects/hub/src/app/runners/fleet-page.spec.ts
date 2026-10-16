import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { FleetPage } from './fleet-page';

const ME = {
  ...OPERATOR_ME_RESPONSE,
  permissions: [...OPERATOR_ME_RESPONSE.permissions, 'runner:retire' as const],
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
  ...over,
});
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

describe('FleetPage', () => {
  let stub: RequestClientStub;

  async function open(url: string, me: unknown = ME) {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (path === '/api/me') return me;
      if (path === '/api/runners') return { runners: [runner('rn_A', { host_id: 'hs_h1' }), runner('rn_solo')] };
      if (path === '/api/hosts') return { hosts: [HOST] };
      if (path === '/api/chunks') return { chunks: [], next_cursor: null };
      if (path === '/api/hosts/hs_h1/enrollments') return { host_id: 'hs_h1', token: 'tok_secret' };
      return {};
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'fleet', component: FleetPage, children: [{ path: ':id', children: [] }] }]),
      ],
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    return { harness, el: harness.fixture.nativeElement as HTMLElement };
  }

  afterEach(() => stub.restore());

  it('opens the first card on a bare /fleet', async () => {
    const { harness, el } = await open('/fleet');
    await settle(harness.fixture);

    expect(TestBed.inject(Router).url).toBe('/fleet/hs_h1');
    expect(el.querySelector('[data-testid="fleet-detail-name"]')?.textContent).toContain('build-box');
  });

  it('lists the host, then the hostless runner under a No host heading', async () => {
    const { el } = await open('/fleet/hs_h1');

    expect(el.querySelector('[data-testid="fleet-card-hs_h1"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="fleet-hostless-heading"]')?.textContent).toContain('No host');
    expect(el.querySelector('[data-testid="fleet-card-rn_solo"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="fleet-card-hs_h1"] [data-testid="fleet-card-ref"]')?.textContent).toContain('H-');
  });

  it('navigates to /fleet/<id> when a card is picked', async () => {
    const { harness, el } = await open('/fleet/hs_h1');

    el.querySelector<HTMLButtonElement>('[data-testid="fleet-card-rn_solo"]')?.click();
    await settle(harness.fixture);

    expect(TestBed.inject(Router).url).toBe('/fleet/rn_solo');
    expect(el.querySelector('[data-testid="fleet-runner-detail"]')).not.toBeNull();
  });

  it('renders a not-found rest state, keeping the list, for an unknown id', async () => {
    const { el } = await open('/fleet/hs_nope');

    expect(el.querySelector('[data-testid="fleet-not-found"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="fleet-card-hs_h1"]')).not.toBeNull();
  });

  it('offers the host verbs and the pause brake, and never a Release', async () => {
    const { el } = await open('/fleet/hs_h1');

    expect(el.querySelector('[data-testid="host-enroll"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="host-retire"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="fleet-runner-toggle"]')).not.toBeNull();
    expect(el.querySelector('[data-testid$="release"]')).toBeNull();
  });

  it('withholds the host verbs without runner:retire', async () => {
    const { el } = await open('/fleet/hs_h1', OPERATOR_ME_RESPONSE);

    expect(el.querySelector('[data-testid="host-verbs"]')).toBeNull();
  });

  it('enrolls the host and shows the token once', async () => {
    const { harness, el } = await open('/fleet/hs_h1');

    el.querySelector<HTMLButtonElement>('[data-testid="host-enroll"]')?.click();
    await settle(harness.fixture);

    expect(stub.forRoute('/api/hosts/hs_h1/enrollments', 'POST')).toHaveLength(1);
    expect(el.querySelector('[data-testid="host-token"]')?.textContent).toContain('tok_secret');
  });

  it('pauses a runner at the hub', async () => {
    const { harness, el } = await open('/fleet/hs_h1');

    el.querySelector<HTMLButtonElement>('[data-testid="fleet-runner-toggle"]')?.click();
    await settle(harness.fixture);

    expect(stub.forRoute('/api/runners/rn_A/pause', 'POST')).toHaveLength(1);
  });
});
