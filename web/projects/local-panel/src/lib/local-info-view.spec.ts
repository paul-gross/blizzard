import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { runnerApi } from 'fleet';

import { LocalInfoView } from './local-info-view';

const RUNNER_STATUS: runnerApi.RunnerStatusView = {
  runner_id: 'runner-local',
  workspace_id: 'workspace-local',
  pause: { local: false, hub: false, effective: false },
  capacities: { max_agents: 4, used: 1, free: 3 },
  hub: {
    endpoint: 'http://127.0.0.1:8421',
    reachable: true,
    last_contact_at: '2026-07-16T11:59:30.000Z',
    buffer_depth: 2,
  },
  last_tick_at: '2026-07-16T11:59:45.000Z',
};

async function render(overrides: Partial<{ fleet: runnerApi.FleetSummaryView | null; fleetStale: boolean }> = {}) {
  await TestBed.configureTestingModule({
    imports: [LocalInfoView],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalInfoView);
  fixture.componentRef.setInput('view', RUNNER_STATUS);
  fixture.componentRef.setInput('lastFlushLabel', '-30s');
  fixture.componentRef.setInput('lastTickLabel', '-15s');
  if (overrides.fleet !== undefined) fixture.componentRef.setInput('fleet', overrides.fleet);
  if (overrides.fleetStale !== undefined) fixture.componentRef.setInput('fleetStale', overrides.fleetStale);
  fixture.detectChanges();
  await fixture.whenStable();
  return { el: fixture.nativeElement as HTMLElement };
}

describe('LocalInfoView', () => {
  it('renders the hub-link facts and the ticking labels the container hands it — no query stub required', async () => {
    const { el } = await render();

    expect(el.querySelector('[data-testid="hub-endpoint"]')?.textContent).toBe(RUNNER_STATUS.hub.endpoint);
    expect(el.querySelector('[data-testid="hub-link"]')?.textContent?.trim()).toBe('CONNECTED');
    expect(el.querySelector('[data-testid="hub-last-flush"]')?.textContent).toContain('-30s');
    expect(el.querySelector('.tick')?.textContent).toContain('-15s');
    expect(el.querySelector('[data-testid="hub-buffered"]')?.textContent).toContain('2 events');
  });

  it('renders the fleet strip live with the given counts', async () => {
    const { el } = await render({ fleet: { ready: 4, running: 3, waiting: 2, needs: 1 }, fleetStale: false });

    expect(el.querySelector('[data-testid="fleet-ready"]')?.textContent).toContain('4');
    expect(el.querySelector('[data-testid="fleet-strip"]')?.classList.contains('stale')).toBe(false);
    expect(el.querySelector('[data-testid="fleet-age"]')?.textContent).toContain('live');
  });

  it('renders the fleet strip dimmed and last-known when stale, without blanking the counts', async () => {
    const { el } = await render({ fleet: { ready: 4, running: 3, waiting: 2, needs: 1 }, fleetStale: true });

    expect(el.querySelector('[data-testid="fleet-strip"]')?.classList.contains('stale')).toBe(true);
    expect(el.querySelector('[data-testid="fleet-age"]')?.textContent).toContain('last known');
    expect(el.querySelector('[data-testid="fleet-ready"]')?.textContent).toContain('4');
  });

  it('renders a dash for every fleet bucket before any fleet summary has arrived', async () => {
    const { el } = await render({ fleet: null, fleetStale: true });

    expect(el.querySelector('[data-testid="fleet-ready"]')?.textContent).toContain('—');
  });
});
