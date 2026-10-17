import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { HostCard } from './host-rows.model';
import type { RunnerRow } from './runner-rows';
import { RunnerPanelView } from './runner-view';

const NOW = new Date().toISOString();

const row = (id: string, over: Partial<RunnerRow> = {}): RunnerRow => ({
  runner_id: id,
  runner_name: `${id}-name`,
  added_at: NOW,
  connection: over.online === false ? 'offline' : 'online',
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


const CARD: HostCard = {
  hostId: 'h1',
  hostName: 'build-box',
  online: true,
  connection: 'online',
  reportLabel: 'seen 4s ago',
  url: null,
  enrolled: true,
  registeredAt: null,
  addedAt: '2026-01-01T00:00:00Z',
  addedBy: null,
  lastSeenLabel: 'never',
  reportedAgo: null,
  heldChunks: new Map(),
  workspaces: [
    { workspaceId: 'ws-1', capacity: 3, inUse: 2, flag: null, environments: [] },
    { workspaceId: 'ws-2', capacity: null, inUse: 0, flag: null, environments: [] },
  ],
  subscriptionPaces: [],
};

const binding = (agents: number, agentsInUse: number) => ({
  project: 'Blizzard',
  workspaceId: 'ws-1',
  agents,
  agentsInUse,
  unreported: false,
});

describe('RunnerPanelView', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [RunnerPanelView],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
  });

  async function render(rows: readonly RunnerRow[], cards: readonly HostCard[] = []): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', rows);
    fixture.componentRef.setInput('cards', cards);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  const label = (el: Element | null) => el?.querySelector('[data-testid="slot-bar-label"]')?.textContent?.trim();

  it('renders each row with its name, liveness and paused state — off plain inputs alone', async () => {
    const el = await render([row('rn_online'), row('rn_paused', { hub_paused: true }), row('rn_local', { locally_paused: true })]);

    expect(el.querySelectorAll('[data-testid="runner"]')).toHaveLength(3);
    const online = el.querySelector('[data-runner="rn_online"]')!;
    expect(online.getAttribute('data-online')).toBe('true');
    expect(online.querySelector('[data-testid="runner-name"]')?.textContent).toContain('rn_online-name');
    expect(el.querySelector('[data-runner="rn_paused"] [data-testid="runner-hub-paused"]')).not.toBeNull();
    expect(el.querySelector('[data-runner="rn_local"] [data-testid="runner-locally-paused"]')).not.toBeNull();
  });

  it('draws a third line only for a paused runner, the badges as soft kit pills', async () => {
    const el = await render([row('rn_free'), row('rn_both', { hub_paused: true, locally_paused: true })]);

    expect(el.querySelector('[data-runner="rn_free"] .r3')).toBeNull();
    const badge = el.querySelector('[data-runner="rn_both"] .r3 [data-testid="runner-hub-paused"]');
    expect(badge?.tagName.toLowerCase()).toBe('fleet-kit-badge');
    expect(badge?.querySelector('.badge')?.classList.contains('soft')).toBe(true);
    expect(el.querySelector('[data-runner="rn_both"] [data-testid="runner-locally-paused"]')).not.toBeNull();
  });

  it('names a spend-ceiling escalation reason on the locally-paused badge', async () => {
    const el = await render([row('rn_ceiling', { locally_paused: true, locally_paused_reason: 'spend ceiling $5.00 reached' })]);

    expect(el.querySelector('[data-testid="runner-locally-paused"]')?.getAttribute('title')).toBe('spend ceiling $5.00 reached');
  });

  it('shows a never-connected runner with a hollow mark and the seen label, not a beacon or a badge', async () => {
    const el = await render([
      row('rn_new', { connection: 'never_connected', online: false, workspace_id: null, registered_at: null, last_seen_at: null }),
      row('rn_gone', { online: false }),
    ]);

    const fresh = el.querySelector('[data-runner="rn_new"]')!;
    expect(fresh.getAttribute('data-connection')).toBe('never_connected');
    expect(fresh.classList.contains('offline')).toBe(false);
    expect(fresh.querySelector('.dot.never')).not.toBeNull();
    expect(fresh.querySelector('fleet-kit-beacon')).toBeNull();
    expect(fresh.querySelector('[data-testid="runner-seen"]')?.textContent).toBe('never connected');
    expect(fresh.querySelector('[data-testid="runner-never-connected"]')).toBeNull();
    expect(el.querySelector('[data-runner="rn_gone"]')?.classList.contains('offline')).toBe(true);
  });

  it('drops everything the Fleet tab owns: id, projects, claims, bars and buttons', async () => {
    const el = await render(
      [
        row('rn_full', {
          claims: [{ chunkId: 'ch_01', shortId: 'C-01', node: 'build', status: 'running' }],
          served: ['Blizzard'],
          env_capacity: 4,
          used: 2,
          gates: ['build'],
          capabilities: [{ harness_id: 'claude', version: '1', tiers: [], default: true, available: true }],
          bindingLines: [binding(3, 1)],
        }),
      ],
      [CARD],
    );

    for (const testid of [
      'runner-id',
      'runner-claim',
      'runner-claims',
      'runner-slot-bar',
      'runner-subscription-groups',
      'runner-capabilities-group',
      'runner-gates-group',
      'runner-toggle',
      'host-card',
    ]) {
      expect(el.querySelector(`[data-testid="${testid}"]`), testid).toBeNull();
    }
    expect(el.querySelector('fleet-kit-button')).toBeNull();
  });

  it('draws the fleet env strip from every host workspace and each host-less pool, once', async () => {
    const el = await render(
      [row('rn_hosted', { host_id: 'h1', bindingLines: [binding(3, 1)] }), row('rn_solo', { env_capacity: 4, used: 1 })],
      [CARD],
    );

    expect(el.querySelectorAll('[data-testid="runner-env-strip"]')).toHaveLength(1);
    expect(label(el.querySelector('[data-testid="runner-env-strip"]'))).toBe('3/7 envs');
  });

  it('omits the env strip when nothing reports a capacity', async () => {
    const el = await render([row('rn_solo', { env_capacity: null })]);

    expect(el.querySelector('[data-testid="runner-env-strip"]')).toBeNull();
  });

  it('draws line 2 as agents in use of declared, and omits it for a runner declaring none', async () => {
    const el = await render([row('rn_bound', { host_id: 'h1', bindingLines: [binding(3, 1), binding(1, 1)] }), row('rn_solo')], [CARD]);

    const bar = el.querySelector('[data-runner="rn_bound"] [data-testid="runner-agents"]');
    expect(label(bar)).toBe('2/4 agents');
    expect(bar?.querySelectorAll('.cell.on')).toHaveLength(2);
    expect(el.querySelector('[data-runner="rn_solo"] [data-testid="runner-agents"]')).toBeNull();
  });

  it('links to the Fleet tab', async () => {
    const el = await render([row('rn_a')]);

    expect(el.querySelector('[data-testid="runner-fleet-link"]')?.getAttribute('href')).toBe('/fleet');
  });

  it('shows the empty state for no rows once loaded', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'empty');
    fixture.componentRef.setInput('rows', []);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="runners-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="runner-env-strip"]')).toBeNull();
  });

  it('withholds the empty copy while the registry read is pending, and shows an error when it fails', async () => {
    const pending = TestBed.createComponent(RunnerPanelView);
    pending.componentRef.setInput('state', 'loading');
    pending.componentRef.setInput('rows', []);
    await pending.whenStable();
    expect((pending.nativeElement as HTMLElement).querySelector('[data-testid="runners-empty"]')).toBeNull();
    expect((pending.nativeElement as HTMLElement).querySelector('[data-testid="runners-loading"]')).not.toBeNull();

    const failed = TestBed.createComponent(RunnerPanelView);
    failed.componentRef.setInput('state', 'error');
    failed.componentRef.setInput('rows', []);
    await failed.whenStable();
    expect((failed.nativeElement as HTMLElement).querySelector('[data-testid="runners-error"]')).not.toBeNull();
  });

  describe('seenLabel (bzh:utc-instants)', () => {
    const REF = Date.parse('2026-07-16T12:00:00.000Z');

    it('reads a fresh heartbeat as "seen Ns ago"', async () => {
      const el = await render([row('r1', { last_seen_at: '2026-07-16T11:59:55.000Z', nowMs: REF })]);

      expect(el.querySelector('[data-testid="runner-seen"]')?.textContent).toBe('seen 5s ago');
    });

    it('does not render a confident 0s for a stamp hours in the future — falls through to online/offline', async () => {
      const el = await render([row('r1', { last_seen_at: '2026-07-16T17:00:00.000Z', online: false, nowMs: REF })]);

      expect(el.querySelector('[data-testid="runner-seen"]')?.textContent).toBe('offline');
    });
  });
});
