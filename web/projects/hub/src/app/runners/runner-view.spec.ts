import { RunnerConnection, type RunnerRegistryView } from 'fleet';
import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { HostCard } from './host-rows.model';
import type { RunnerRow } from './runner-rows.model';
import { RunnerPanelView } from './runner-view';

const REGISTRY: RunnerRegistryView = {
  agents: null,
  runner_id: '',
  runner_name: '',
  added_at: '',
  added_by: null,
  bindings: null,
  capabilities: [],
  connection: RunnerConnection.NEVER_CONNECTED,
  declining: [],
  env_capacity: null,
  gates: [],
  host_id: null,
  hub_paused: false,
  last_seen_at: null,
  locally_paused: false,
  locally_paused_by: null,
  locally_paused_reason: null,
  online: false,
  projects: [],
  registered_at: null,
  retired: false,
  retired_at: null,
  retired_by: null,
  subscription: null,
  subscription_unrostered: false,
  subscriptions: [],
  version: null,
  version_refusal: null,
  version_standing: null,
  workspace_id: null,
};


const NOW = new Date().toISOString();

const row = (id: string, over: Partial<RunnerRow> = {}): RunnerRow => ({
  ...REGISTRY,
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

const binding = (agents: number, agentsInUse: number, project = 'Blizzard') => ({
  project,
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

  const label = (el: Element | null) => el?.querySelector('[data-testid="slot-bar-gauge"]')?.getAttribute('title');

  it('renders each row with its name, liveness and paused state — off plain inputs alone', async () => {
    const el = await render([row('rn_online'), row('rn_paused', { hub_paused: true }), row('rn_local', { locally_paused: true })]);

    expect(el.querySelectorAll('[data-testid="runner"]')).toHaveLength(3);
    const online = el.querySelector('[data-runner="rn_online"]')!;
    expect(online.getAttribute('data-online')).toBe('true');
    expect(online.querySelector('[data-testid="runner-name"]')?.textContent).toContain('rn_online-name');
    expect(el.querySelector('[data-runner="rn_paused"] [data-testid="runner-hub-paused"]')).not.toBeNull();
    expect(el.querySelector('[data-runner="rn_local"] [data-testid="runner-locally-paused"]')).not.toBeNull();
  });

  it('glyphs each brake after the name, outside its link — pause for the runner, stop for the hub', async () => {
    const el = await render([row('rn_free'), row('rn_both', { hub_paused: true, locally_paused: true })]);

    expect(el.querySelector('[data-runner="rn_free"] fleet-kit-glyph')).toBeNull();
    const name = el.querySelector('[data-runner="rn_both"] [data-testid="runner-name"]')!;
    const hub = name.querySelector('[data-testid="runner-hub-paused"]');
    const local = name.querySelector('[data-testid="runner-locally-paused"]');
    expect(hub?.getAttribute('data-glyph')).toBe('stop');
    expect(local?.getAttribute('data-glyph')).toBe('pause');
    expect(hub?.getAttribute('title')).toBe('Paused by the hub — no new claims');
    expect(name.querySelector('[data-testid="runner-fleet-link"] fleet-kit-glyph')).toBeNull();
    expect(el.querySelector('fleet-kit-badge')).toBeNull();
  });

  it('lights each runner — steady green online, steady yellow when either brake holds it, blinking red offline', async () => {
    const el = await render([
      row('rn_up'),
      row('rn_held', { locally_paused: true }),
      row('rn_stopped', { hub_paused: true }),
      row('rn_down', { online: false, hub_paused: true }),
    ]);

    const tone = (id: string) => el.querySelector(`[data-runner="${id}"] [data-testid="runner-light"]`)?.getAttribute('data-tone');
    expect(['rn_up', 'rn_held', 'rn_stopped', 'rn_down'].map(tone)).toEqual(['green', 'amber', 'amber', 'red']);
    expect(el.querySelector('[data-runner="rn_up"] .beacon')?.classList.contains('hold')).toBe(true);
    expect(['rn_up', 'rn_held', 'rn_stopped'].map((id) => el.querySelector(`[data-runner="${id}"]`)?.classList.contains('paused'))).toEqual([
      false,
      true,
      true,
    ]);
    expect(el.querySelector('[data-runner="rn_down"] .beacon')?.classList.contains('step')).toBe(true);
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
    expect(fresh.querySelector('[data-testid="runner-light"] .hollow')).not.toBeNull();
    expect(fresh.querySelector('fleet-kit-beacon')).toBeNull();
    expect(fresh.querySelector('[data-testid="runner-seen"]')?.textContent).toBe('[never]');
    expect(fresh.querySelector('[data-testid="runner-seen"]')?.getAttribute('title')).toBe('never connected');
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

  it('draws one env strip per pool — each host workspace reporting a capacity, then each host-less pool', async () => {
    const el = await render(
      [row('rn_hosted', { host_id: 'h1', bindingLines: [binding(3, 1)] }), row('rn_solo', { env_capacity: 4, used: 1 })],
      [CARD],
    );

    const strips = [...el.querySelectorAll('[data-testid="runner-env-strip"]')];
    expect(strips.map((strip) => strip.querySelector('[data-testid="slot-bar-name"]')?.textContent?.trim())).toEqual(['ws-1', 'ws_a']);
    expect(strips.map(label)).toEqual(['2/3 envs', '1/4 envs']);
  });

  it('omits the env strip when nothing reports a capacity', async () => {
    const el = await render([row('rn_solo', { env_capacity: null })]);

    expect(el.querySelector('[data-testid="runner-env-strip"]')).toBeNull();
  });

  it('draws one agents bar per runner, and none for a runner declaring none', async () => {
    const el = await render(
      [row('rn_bound', { host_id: 'h1', bindingLines: [binding(2, 2, 'blizzard'), binding(1, 0, 'winter')] }), row('rn_solo')],
      [CARD],
    );

    const bars = [...el.querySelectorAll('[data-runner="rn_bound"] [data-testid="runner-agents"]')];
    expect(bars.map((bar) => bar.querySelector('[data-testid="slot-bar-name"]')?.textContent?.trim())).toEqual(['agents']);
    expect(bars.map(label)).toEqual(['2/3 agents']);
    expect(el.querySelector('[data-runner="rn_solo"] [data-testid="runner-agents"]')).toBeNull();
  });

  it('names the host a hosted runner runs on, and none for a host-less one', async () => {
    const el = await render([row('rn_hosted', { host_id: 'h1' }), row('rn_solo')], [CARD]);

    expect(el.querySelector('[data-runner="rn_hosted"] [data-testid="runner-host"]')?.textContent?.trim()).toBe('build-box');
    expect(el.querySelector('[data-runner="rn_solo"] [data-testid="runner-host"]')).toBeNull();
  });

  it('links each name to the Fleet tab — a hosted runner to its host, a host-less one to itself — with no footer link', async () => {
    const el = await render([row('rn_hosted', { host_id: 'h1' }), row('rn_solo')], [CARD]);

    const href = (id: string) => el.querySelector(`[data-runner="${id}"] [data-testid="runner-fleet-link"]`)?.getAttribute('href');
    expect(href('rn_hosted')).toBe('/fleet/h1');
    expect(href('rn_solo')).toBe('/fleet/rn_solo');
    expect(el.querySelector('[data-runner="rn_solo"] [data-testid="runner-fleet-link"]')?.textContent?.trim()).toBe('rn_solo-name');
    expect(el.querySelectorAll('[data-testid="runner-fleet-link"]')).toHaveLength(2);
  });

  it('leads with one pace line per distinct subscription, its reading’s age under its name and its figures in each window’s tooltip, then the runners, then the env strips', async () => {
    const paces = [
      {
        slug: 'claude',
        name: 'Claude Max',
        paceBars: [{ window: '5h', utilizationPct: 90, elapsedPct: 40 }],
        condition: null,
        sampledAt: NOW,
        refreshedLabel: 'refreshed 50s ago',
        refreshedTag: '[50s]',
        freshness: 'fresh' as const,
        missReason: null,
      },
    ];
    const el = await render([row('rn_hosted', { host_id: 'h1' })], [{ ...CARD, subscriptionPaces: paces }]);

    const order = [...el.querySelectorAll('[data-testid="runner-subscriptions"], [data-testid="runner-list"], .envs')].map((n) =>
      n.getAttribute('data-testid') ?? 'envs',
    );
    expect(order).toEqual(['runner-subscriptions', 'runner-list', 'envs']);
    const subs = el.querySelectorAll('[data-testid="runner-subscription"]');
    expect(subs).toHaveLength(1);
    expect(subs[0].querySelector('[data-testid="subscription-blocks-name"]')?.textContent?.trim()).toBe('Claude Max');
    expect(subs[0].querySelector('[data-testid="subscription-blocks-age"]')?.textContent?.trim()).toBe('[50s]');
    const window = subs[0].querySelector('[data-testid="subscription-blocks-window"]')!;
    expect(window.getAttribute('title')).toBe('5h · 90% used · 40% of window');
    expect(window.classList.contains('ahead')).toBe(true);
    expect(window.querySelectorAll('.cell.on')).toHaveLength(9);
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

  describe('seen tag (bzh:utc-instants)', () => {
    const REF = Date.parse('2026-07-16T12:00:00.000Z');

    it('reads a fresh heartbeat as a bracketed age, the words in its tooltip', async () => {
      const el = await render([row('r1', { last_seen_at: '2026-07-16T11:59:55.000Z', nowMs: REF })]);

      const seen = el.querySelector('[data-testid="runner-seen"]');
      expect(seen?.textContent).toBe('[5s]');
      expect(seen?.getAttribute('title')).toBe('seen 5s ago');
    });

    it('does not render a confident 0s for a stamp hours in the future — a dash, offline in its tooltip', async () => {
      const el = await render([row('r1', { last_seen_at: '2026-07-16T17:00:00.000Z', online: false, nowMs: REF })]);

      const seen = el.querySelector('[data-testid="runner-seen"]');
      expect(seen?.textContent).toBe('[—]');
      expect(seen?.getAttribute('title')).toBe('offline');
    });
  });
});
