import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { type MeResponse, type RunnerRegistryView } from 'fleet';
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

const ME: MeResponse = {
  ...OPERATOR_ME_RESPONSE,
  permissions: [...OPERATOR_ME_RESPONSE.permissions, 'runner:retire' as const],
};
/** A `contributor`'s `/api/me` — every day-to-day operating permission, but not the
 * admin-tier `runner:pause`. */
const CONTRIBUTOR_ME: MeResponse = {
  ...OPERATOR_ME_RESPONSE,
  role: 'contributor',
  permissions: OPERATOR_ME_RESPONSE.permissions.filter((p) => p !== 'runner:pause' && p !== 'graph:edit' && p !== 'user:manage'),
};
const NOW = new Date().toISOString();
const runner = (id: string, over: Partial<RunnerRegistryView> = {}) =>
  runnerRegistryView({ runner_id: id, registered_at: NOW, last_seen_at: NOW, ...over });
const HOST = hostRegistryView({ host_id: 'hs_h1', host_name: 'build-box', added_at: NOW, last_seen_at: NOW });

type Routes = Parameters<typeof stubHubClient>[0];

describe('FleetPage', () => {
  let stub: RequestClientStub;

  async function open(url: string, me: MeResponse = ME, over: Routes = {}) {
    stub = stubHubClient({
      'GET /api/me': () => me,
      'GET /api/runners': () => ({
        runners: [
          runner('rn_A', { host_id: 'hs_h1' }),
          runner('rn_solo', { env_capacity: 4, hub_paused: false }),
          runner('rn_stopped', { hub_paused: true }),
        ],
      }),
      'GET /api/hosts': () => ({ hosts: [HOST] }),
      'GET /api/chunks': () => ({ chunks: [], next_cursor: null }),
      'POST /api/hosts/{host_id}/enrollments': () => ({ host_id: 'hs_h1', token: 'tok_secret' }),
      'POST /api/runners/{runner_id}/pause': (_request, { runner_id }) => runner(runner_id, { hub_paused: true }),
      'POST /api/runners/{runner_id}/resume': (_request, { runner_id }) => runner(runner_id),
      ...over,
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
    const { el } = await open('/fleet/rn_solo', ME, {
      'GET /api/runners': () => ({
        runners: [runner('rn_solo', { online: false, connection: 'never_connected', registered_at: null, last_seen_at: null })],
      }),
    });

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

    const rejectPause: Routes = {
      'POST /api/runners/{runner_id}/pause': () => stubError(409, { detail: 'runner already paused' }),
    };

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
