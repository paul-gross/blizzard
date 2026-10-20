import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';
import { FleetPage } from './fleet-page';

const ME = {
  ...OPERATOR_ME_RESPONSE,
  permissions: [...OPERATOR_ME_RESPONSE.permissions, 'runner:retire' as const],
};
/** A `contributor`'s `/api/me` — every day-to-day operating permission, but not the
 * admin-tier `runner:pause`. */
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

  async function open(url: string, me: unknown = ME, over: (method: string, path: string) => unknown = () => undefined) {
    stub = stubRequestClient(hubClient, (method, path) => {
      const hit = over(method, path);
      if (hit !== undefined) return hit;
      if (path === '/api/me') return me;
      if (path === '/api/runners') {
        return {
          runners: [
            runner('rn_A', { host_id: 'hs_h1' }),
            runner('rn_solo', { env_capacity: 4, hub_paused: false }),
            runner('rn_stopped', { hub_paused: true }),
          ],
        };
      }
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

  it('shows a hostless runner its env slot bar, which no host card carries for it', async () => {
    const { el } = await open('/fleet/rn_solo');

    expect(el.querySelector('[data-testid="fleet-runner-slot-bar"] [data-testid="slot-bar-label"]')?.textContent).toContain('0/4');
  });

  it('gives a hosted runner no slot bar of its own — its host card owns the pool', async () => {
    const { el } = await open('/fleet/hs_h1');

    expect(el.querySelector('[data-testid="fleet-runner-slot-bar"]')).toBeNull();
    expect(el.querySelector('[data-testid="fleet-runner-subscription-groups"]')).toBeNull();
  });

  it('marks a never-connected hostless runner with a hollow light and no beacon', async () => {
    const { el } = await open('/fleet/rn_solo', ME, (_method, path) =>
      path === '/api/runners'
        ? { runners: [runner('rn_solo', { online: false, connection: 'never_connected', registered_at: null, last_seen_at: null })] }
        : undefined,
    );

    expect(el.querySelector('[data-testid="fleet-runner"] .dot .hollow')).not.toBeNull();
    expect(el.querySelector('[data-testid="fleet-runner"] fleet-kit-beacon')).toBeNull();
  });

  describe('pause and resume', () => {
    const toggle = (el: HTMLElement, id: string): HTMLButtonElement | null =>
      el.querySelector<HTMLButtonElement>(`[data-runner="${id}"] [data-testid="fleet-runner-toggle"]`);

    /** Holds the mutation's settling refetch open so the pending state can be read. */
    function holdInvalidation(): () => void {
      let resolveInvalidate!: () => void;
      vi.spyOn(TestBed.inject(QueryClient), 'invalidateQueries').mockReturnValue(
        new Promise<void>((resolve) => (resolveInvalidate = resolve)),
      );
      return () => resolveInvalidate();
    }

    const rejectPause = (method: string, path: string) =>
      path === '/api/runners/rn_solo/pause' ? stubError(409, { detail: 'runner already paused' }) : undefined;

    it('resumes a hub-paused runner at the hub', async () => {
      const { harness, el } = await open('/fleet/rn_stopped');

      toggle(el, 'rn_stopped')?.click();
      await settle(harness.fixture);

      expect(stub.forRoute('/api/runners/rn_stopped/resume', 'POST')).toHaveLength(1);
    });

    it("disables only the clicked runner's toggle while its mutation is pending", async () => {
      const { harness, el } = await open('/fleet/rn_solo');
      const release = holdInvalidation();

      toggle(el, 'rn_solo')?.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
      harness.fixture.detectChanges();

      expect(toggle(el, 'rn_solo')?.disabled).toBe(true);
      release();
      await settle(harness.fixture);
      expect(toggle(el, 'rn_solo')?.disabled).toBe(false);
    });

    it('renders the requested hub_paused while the pause is pending', async () => {
      const { harness, el } = await open('/fleet/rn_solo');
      const release = holdInvalidation();

      toggle(el, 'rn_solo')?.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
      harness.fixture.detectChanges();

      expect(el.querySelector('[data-runner="rn_solo"] [data-testid="fleet-runner-hub-paused"]')).not.toBeNull();
      expect(toggle(el, 'rn_solo')?.textContent?.trim()).toBe('Resume');
      release();
      await settle(harness.fixture);
    });

    it('reverts the override and reports the failure when the pause is rejected', async () => {
      const { harness, el } = await open('/fleet/rn_solo', ME, rejectPause);
      const release = holdInvalidation();

      toggle(el, 'rn_solo')?.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
      harness.fixture.detectChanges();
      release();
      await settle(harness.fixture);

      expect(el.querySelector('[data-runner="rn_solo"] [data-testid="fleet-runner-hub-paused"]')).toBeNull();
      expect(toggle(el, 'rn_solo')?.textContent?.trim()).toBe('Pause');
      expect(el.querySelector('[data-testid="fleet-action-error"]')?.textContent).toBe('runner already paused');
    });

    it('withholds the brake from an identity without runner:pause, the runner still rendering', async () => {
      const { el } = await open('/fleet/rn_stopped', CONTRIBUTOR_ME);

      expect(el.querySelector('[data-runner="rn_stopped"] [data-testid="fleet-runner-hub-paused"]')).not.toBeNull();
      expect(el.querySelectorAll('[data-testid="fleet-runner-toggle"]')).toHaveLength(0);
    });
  });
});
