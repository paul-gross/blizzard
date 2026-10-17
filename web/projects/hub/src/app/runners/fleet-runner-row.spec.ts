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

  it('leaves the host-less runner’s slot bar and pace bars to its detail pane', async () => {
    const { el } = await render(row('rn_cap', { env_capacity: 4, used: 2 }));

    expect(el.querySelector('[data-testid="fleet-runner-slot-bar"]')).toBeNull();
    expect(el.querySelector('[data-testid="fleet-runner-subscription-groups"]')).toBeNull();
  });

  it('heads the row with its harnesses and agents, and says none for an empty binds line', async () => {
    const { el } = await render(
      row('rn_sum', {
        capabilities: [{ harness_id: 'claude-code', available: true }],
        bindingLines: [{ project: 'Blizzard', workspaceId: 'ws_a', agents: 3, agentsInUse: 1, unreported: false }],
      }),
      { hosted: true },
    );
    expect(el.querySelector('[data-testid="fleet-runner-summary"]')?.textContent).toMatch(/3 agents · 1 busy$/);

    const empty = await render(row('rn_empty'), { hosted: true });
    expect(empty.el.querySelector('.none-inline')?.textContent).toBe('none');
  });

  it('names a hosted runner’s subscription, flagged when its host’s roster lacks it, and none for a runner with no host', async () => {
    const { el } = await render(row('rn_spend', { subscriptionName: 'Claude Max', subscription_unrostered: true }), { hosted: true });
    const spends = el.querySelector('[data-testid="runner-subscription"]');
    expect(spends?.querySelector('[data-testid="runner-subscription-name"]')?.textContent?.trim()).toBe('Claude Max');
    expect(spends?.querySelector('[data-testid="runner-subscription-unrostered"]')?.textContent?.trim()).toBe('UNROSTERED');

    const hostless = await render(row('rn_alone', { subscriptionName: 'Claude Max' }));
    expect(hostless.el.querySelector('[data-testid="runner-subscription"]')).toBeNull();
  });

  it('states a live runner as working or idle by its claims, an offline one as such, and says when it holds no chunk', async () => {
    const claim = { chunkId: 'ch_1', shortId: 'C-0001', node: 'build', status: 'running' };
    const working = await render(row('rn_busy', { claims: [claim] } as unknown as Partial<RunnerRow>));
    expect(working.el.querySelector('[data-testid="fleet-runner-claims"]')?.textContent).toContain('C-0001');
    expect(working.el.querySelector('.badges')?.textContent?.trim()).toBe('WORKING');

    const idle = await render(row('rn_idle'));
    expect(idle.el.querySelector('.badges')?.textContent?.trim()).toBe('IDLE');
    expect(idle.el.querySelector('[data-testid="fleet-runner-no-claims"]')?.textContent?.trim()).toBe('HOLDS NO CHUNKS');

    const offline = await render(row('rn_gone', { online: false, connection: 'offline' }));
    expect(offline.el.querySelector('.badges')?.textContent?.trim()).toBe('OFFLINE');
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
