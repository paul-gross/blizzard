import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { RunnerRow } from './runner-rows';
import { FleetRunnerRow } from './fleet-runner-row';

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

describe('FleetRunnerRow', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [FleetRunnerRow],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  async function render(r: RunnerRow, inputs: Record<string, unknown> = {}) {
    const fixture = TestBed.createComponent(FleetRunnerRow);
    fixture.componentRef.setInput('row', r);
    for (const [name, value] of Object.entries(inputs)) fixture.componentRef.setInput(name, value);
    await fixture.whenStable();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  it('ends each claim with the chunk status', async () => {
    const { el } = await render(row('rn_a', { claims: [{ chunkId: 'ch_01', shortId: 'C-01', node: 'build', status: 'running' }] }));

    const claim = el.querySelector('[data-testid="fleet-runner-claims"] .claim');
    expect(claim?.textContent).toContain('build');
    expect(claim?.textContent).toContain('running');
  });

  it('names the hub brake and the runner’s own, with the spend-ceiling reason on the latter', async () => {
    const { el } = await render(row('rn_a', { hub_paused: true, locally_paused: true, locally_paused_reason: 'spend ceiling $5.00 reached' }));

    expect(el.querySelector('[data-testid="fleet-runner-hub-paused"]')).not.toBeNull();
    expect(el.querySelector('[title="spend ceiling $5.00 reached"]')?.textContent).toContain('LOCALLY PAUSED');
  });

  it('shows a never-connected runner with the badge, a hollow mark and no beacon', async () => {
    const { el } = await render(row('rn_new', { connection: 'never_connected', online: false, registered_at: null, last_seen_at: null }));

    expect(el.textContent).toContain('NEVER CONNECTED');
    expect(el.querySelector('[data-testid="fleet-runner-seen"]')?.textContent).toBe('never connected');
    expect(el.querySelector('.dot.never')).not.toBeNull();
    expect(el.querySelector('fleet-kit-beacon')).toBeNull();
  });

  it('lights an online runner green and an offline one red', async () => {
    const online = await render(row('rn_on'));
    expect(online.el.querySelector('fleet-kit-beacon .beacon')?.getAttribute('style')).toContain('var(--green)');
    expect(online.el.querySelector('fleet-kit-beacon .beacon')?.classList.contains('step')).toBe(false);

    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({ imports: [FleetRunnerRow], providers: [provideZonelessChangeDetection()] }).compileComponents();
    const offline = await render(row('rn_off', { connection: 'offline', online: false }));
    expect(offline.el.querySelector('fleet-kit-beacon .beacon')?.getAttribute('style')).toContain('var(--red)');
    expect(offline.el.querySelector('fleet-kit-beacon .beacon')?.classList.contains('step')).toBe(true);
  });

  it('renders a slot bar for a host-less runner’s reported capacity, with used filled of total', async () => {
    const { el } = await render(row('rn_cap', { env_capacity: 4, used: 2 }));

    const bar = el.querySelector('[data-testid="fleet-runner-slot-bar"]');
    expect(bar?.querySelectorAll('.cell')).toHaveLength(4);
    expect(bar?.querySelectorAll('.cell.on')).toHaveLength(2);
    expect(bar?.querySelector('[data-testid="slot-bar-label"]')?.textContent?.trim()).toBe('2/4 slots');
  });

  it('omits the slot bar for a null capacity and for a hosted runner', async () => {
    const nullCap = await render(row('rn_nocap', { env_capacity: null }));
    expect(nullCap.el.querySelector('[data-testid="fleet-runner-slot-bar"]')).toBeNull();

    const hosted = await render(row('rn_hosted', { env_capacity: 4 }), { hosted: true });
    expect(hosted.el.querySelector('[data-testid="fleet-runner-slot-bar"]')).toBeNull();
  });

  it('renders per-subscription pace bars for a host-less runner, none when it has no subscription', async () => {
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
    const bars = paced.el.querySelectorAll('[data-testid="fleet-runner-subscription-groups"] [data-testid="runner-pace-bar"]');
    expect([...bars].map((b) => b.getAttribute('data-pace-window'))).toEqual(['5h', '7d']);

    const bare = await render(row('rn_bare'));
    expect(bare.el.querySelector('[data-testid="fleet-runner-subscription-groups"]')).toBeNull();
  });

  it('renders a hosted runner’s binding lines', async () => {
    const { el } = await render(
      row('rn_bound', { bindingLines: [{ project: 'Blizzard', workspaceId: 'ws_a', agents: 3, agentsInUse: 1, unreported: false }] }),
      { hosted: true },
    );

    expect(el.querySelector('[data-testid="runner-binding"]')?.textContent).toContain('Blizzard');
  });

  it('renders one badge per capability and per gate', async () => {
    const { el } = await render(
      row('rn_caps', {
        capabilities: [
          { harness_id: 'claude', version: '1.2.3', tiers: ['sonnet'], default: true, available: true },
          { harness_id: 'codex', version: '4.5.6', tiers: [], default: false, available: false },
        ],
        gates: ['build', 'review'],
      }),
    );

    const caps = el.querySelector('[data-testid="fleet-runner-capabilities"]');
    expect(caps?.querySelector('[data-harness-id="claude"]')?.getAttribute('data-available')).toBe('true');
    expect(caps?.querySelector('[data-harness-id="codex"]')?.getAttribute('data-available')).toBe('false');
    const gates = el.querySelector('[data-testid="fleet-runner-gates"]');
    expect([...gates!.querySelectorAll('[data-testid="runner-gate-badge"]')].map((b) => b.textContent?.trim())).toEqual(['build', 'review']);
  });

  it('emits togglePause with the row, only while the brake is offered', async () => {
    const r = row('rn_a');
    const { fixture, el } = await render(r, { canPause: true });
    const emitted: RunnerRow[] = [];
    fixture.componentInstance.togglePause.subscribe((value) => emitted.push(value));

    el.querySelector<HTMLButtonElement>('[data-testid="fleet-runner-toggle"]')?.click();
    expect(emitted).toEqual([r]);

    const locked = await render(r);
    expect(locked.el.querySelector('[data-testid="fleet-runner-toggle"]')).toBeNull();
  });
});
