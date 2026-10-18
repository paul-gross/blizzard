import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { RunnerDetailVm } from './fleet-page.model';
import { HostlessRunnerDetail } from './hostless-runner-detail';
import type { RunnerRow } from './runner-rows.model';

const NOW = new Date().toISOString();

const row = (id: string, over: Partial<RunnerRow> = {}): RunnerRow => ({
  runner_id: id,
  runner_name: `${id}-name`,
  added_at: NOW,
  connection: 'online',
  workspace_id: 'ws_a',
  registered_at: NOW,
  last_seen_at: NOW,
  online: true,
  hub_paused: false,
  locally_paused: false,
  claims: [],
  used: 0,
  subscriptionPaces: [],
  nowMs: Date.parse(NOW),
  served: [],
  unassigned: false,
  unresolved: [],
  bindingLines: [],
  subscriptionName: null,
  ...over,
});

const detail = (r: RunnerRow): RunnerDetailVm => ({
  kind: 'runner',
  row: r,
  ref: 'R-0001',
  facts: [{ label: 'Workspace', value: r.workspace_id ?? '—', testid: 'fleet-fact-workspace' }],
});

describe('HostlessRunnerDetail', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HostlessRunnerDetail],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  async function render(r: RunnerRow) {
    const fixture = TestBed.createComponent(HostlessRunnerDetail);
    fixture.componentRef.setInput('detail', detail(r));
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('renders a slot bar for the runner’s reported capacity, with used filled of total', async () => {
    const el = await render(row('rn_cap', { env_capacity: 4, used: 2 }));

    const bar = el.querySelector('[data-testid="fleet-runner-slot-bar"]');
    expect(bar?.querySelectorAll('.cell')).toHaveLength(4);
    expect(bar?.querySelectorAll('.cell.on')).toHaveLength(2);
    expect(el.querySelector('[data-testid="fleet-runner-workspace"]')?.textContent).toContain('2 / 4 slots');
  });

  it('states the workspace it runs in', async () => {
    const el = await render(row('rn_ws'));

    expect(el.querySelector('[data-testid="fleet-fact-workspace"]')?.textContent).toContain('ws_a');
  });

  it('omits the slot bar for a null capacity', async () => {
    const el = await render(row('rn_nocap', { env_capacity: null }));

    expect(el.querySelector('[data-testid="fleet-runner-slot-bar"]')).toBeNull();
  });

  it('renders per-subscription pace bars, none when it has no subscription', async () => {
    const paced = await render(
      row('rn_paced', {
        subscriptionPaces: [
          {
            slug: 'anthropic-default',
            name: 'Anthropic (default)',
            paceBars: [
              { window: '5h', utilizationPct: 40, elapsedPct: 20 },
              { window: '7d', utilizationPct: 70, elapsedPct: 55 },
            ],
            condition: null,
            sampledAt: NOW,
            refreshedLabel: 'refreshed 0s ago',
            freshness: 'fresh',
            missReason: null,
          },
        ],
      }),
    );
    const bars = paced.querySelectorAll('[data-testid="fleet-runner-subscription-groups"] [data-testid="runner-pace-bar"]');
    expect([...bars].map((b) => b.getAttribute('data-pace-window'))).toEqual(['5h', '7d']);

    const bare = await render(row('rn_bare'));
    expect(bare.querySelector('[data-testid="fleet-runner-subscription-groups"]')).toBeNull();
    expect(bare.querySelector('[data-testid="fleet-subscriptions-empty"]')?.textContent?.trim()).toBe('NO SUBSCRIPTIONS REPORTED');
  });

  it('names a never-connected runner once in its title', async () => {
    const el = await render(row('rn_new', { connection: 'never_connected', online: false, last_seen_at: null }));

    expect(el.querySelector('[data-testid="fleet-detail-state"]')?.textContent?.trim()).toBe('NEVER CONNECTED');
    expect(el.querySelector('[data-testid="fleet-detail-report"]')?.textContent?.trim()).toBe('stands alone');
  });
});
