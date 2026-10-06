import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { runnerApi } from 'fleet';

import { LocalInfoView } from './app-info-view';

const RUNNER_STATUS: runnerApi.RunnerStatusView = {
  runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNABF3',
  runner_name: 'runner-local',
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

async function render(
  overrides: Partial<{
    fleet: runnerApi.FleetSummaryView | null;
    fleetStale: boolean;
    gates: string[];
    identity: Pick<runnerApi.RunnerStatusView, 'runner_id' | 'runner_name'>;
  }> = {},
) {
  await TestBed.configureTestingModule({
    imports: [LocalInfoView],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalInfoView);
  fixture.componentRef.setInput('view', { ...RUNNER_STATUS, ...overrides.identity, gates: overrides.gates ?? [] });
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

  it('names the runner beside its full hub-minted id', async () => {
    const { el } = await render();

    expect(el.querySelector('[data-testid="runner-identity"]')?.textContent?.trim()).toBe(
      'runner-local · rn_01KXKVVF1J3D6H6VYZ3XYNABF3',
    );
  });

  it('keeps the full id whole in its own nowrap element, out of the value column\'s anywhere-wrap', async () => {
    const { el } = await render();

    // jsdom lays nothing out; the shell sweep proves the id stays on one line in a real column.
    const id = el.querySelector<HTMLElement>('[data-testid="runner-identity"] .rid')!;
    expect(id.textContent).toBe('rn_01KXKVVF1J3D6H6VYZ3XYNABF3');
    expect(id.getAttribute('title')).toBe('rn_01KXKVVF1J3D6H6VYZ3XYNABF3');
    expect(getComputedStyle(id).whiteSpace).toBe('nowrap');
  });

  it('shows its configured name as not registered before the first registration', async () => {
    const { el } = await render({ identity: { runner_id: null, runner_name: 'runner-local' } });

    expect(el.querySelector('[data-testid="runner-identity"]')?.textContent?.trim()).toBe('runner-local · not registered');
    expect(el.querySelector('[data-testid="runner-identity"] .rid')).toBeNull();
  });

  it('renders none when the runner imposes no gate', async () => {
    const { el } = await render();

    expect(el.querySelector('[data-testid="hub-gates"]')?.textContent?.trim()).toBe('none');
  });

  it('renders the gates this runner imposes', async () => {
    const { el } = await render({ gates: ['build', 'review'] });

    expect(el.querySelector('[data-testid="hub-gates"]')?.textContent?.trim()).toBe('build, review');
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
