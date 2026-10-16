import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

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

describe('RunnerPanelView', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [RunnerPanelView],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders each row with its liveness, paused state, and claims — off plain inputs alone', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_online', { claims: [{ chunkId: 'ch_01', shortId: 'C-01', node: 'build', status: 'running' }] }),
      row('rn_paused', { hub_paused: true }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="runner"]')).toHaveLength(2);
    expect(el.querySelector('[data-runner="rn_online"]')?.getAttribute('data-online')).toBe('true');
    const claims = el.querySelectorAll('[data-runner="rn_online"] [data-testid="runner-claim"]');
    expect(claims).toHaveLength(1);
    expect(claims[0].textContent).toContain('build');
    // The line ends with the chunk's status (#156); its tone is asserted in the container spec.
    expect(claims[0].textContent).toContain('running');
    expect(el.querySelector('[data-runner="rn_paused"] [data-testid="runner-hub-paused"]')).not.toBeNull();
  });

  it('names each runner beside its full id, keeping two runners that share a name apart', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_01KXKVVF1J3D6H6VYZ3XYNABF3', { runner_name: 'r-claude' }),
      row('rn_01KXKVVF1J3D6H6VYZ3XYN7Q2M', { runner_name: 'r-claude' }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    for (const id of ['rn_01KXKVVF1J3D6H6VYZ3XYNABF3', 'rn_01KXKVVF1J3D6H6VYZ3XYN7Q2M']) {
      expect(el.querySelector(`[data-runner="${id}"] [data-testid="runner-name"]`)?.textContent?.trim()).toBe('r-claude');
      expect(el.querySelector(`[data-runner="${id}"] [data-testid="runner-id"]`)?.textContent?.trim()).toBe(id);
    }
  });

  it('shows a never-connected runner as never connected, not offline, with no workspace yet', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_new', { connection: 'never_connected', online: false, workspace_id: null, registered_at: null, last_seen_at: null }),
      row('rn_gone', { online: false }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const fresh = el.querySelector('[data-runner="rn_new"]') as HTMLElement;
    expect(fresh.getAttribute('data-connection')).toBe('never_connected');
    expect(fresh.classList.contains('offline')).toBe(false);
    const badge = fresh.querySelector('[data-testid="runner-never-connected"]');
    expect(badge?.textContent).toContain('NEVER CONNECTED');
    // The kit's calm soft pill, as the mobile registry draws the same state.
    expect(badge?.tagName.toLowerCase()).toBe('fleet-kit-badge');
    expect(badge?.querySelector('.badge')?.classList.contains('soft')).toBe(true);
    expect(fresh.querySelector('[data-testid="runner-seen"]')?.textContent).toBe('never connected');
    expect(fresh.querySelector('.wid')?.textContent).toBe('—');
    const gone = el.querySelector('[data-runner="rn_gone"]') as HTMLElement;
    expect(gone.classList.contains('offline')).toBe(true);
    expect(gone.querySelector('[data-testid="runner-never-connected"]')).toBeNull();
  });

  it('shows the empty state for no rows once loaded', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'empty');
    fixture.componentRef.setInput('rows', []);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="runners-empty"]')).not.toBeNull();
  });

  it('withholds the empty copy while the registry read is pending (AC 3)', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'loading');
    fixture.componentRef.setInput('rows', []);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="runners-loading"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="runners-empty"]')).toBeNull();
  });

  it('shows an error state when the registry read fails', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'error');
    fixture.componentRef.setInput('rows', []);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="runners-error"]')).not.toBeNull();
  });

  it('names a spend-ceiling escalation reason on the locally-paused badge (#61)', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_ceiling', { locally_paused: true, locally_paused_reason: 'spend ceiling $5.00 reached' }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="runner-locally-paused"]')?.getAttribute('title')).toBe(
      'spend ceiling $5.00 reached',
    );
  });

  it('renders a slot bar for a reported capacity, with used filled of total (#69)', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [row('rn_cap', { env_capacity: 4, used: 2 })]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const bar = el.querySelector('[data-runner="rn_cap"] [data-testid="runner-slot-bar"]');
    expect(bar).not.toBeNull();
    expect(bar?.querySelectorAll('.cell')).toHaveLength(4);
    expect(bar?.querySelectorAll('.cell.on')).toHaveLength(2);
    expect(bar?.querySelector('[data-testid="slot-bar-label"]')?.textContent?.trim()).toBe('2/4 slots');
  });

  it('omits the slot bar for a runner whose capacity is null (older client) (#69)', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [row('rn_nocap', { env_capacity: null, used: 0 })]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-runner="rn_nocap"] [data-testid="runner-slot-bar"]')).toBeNull();
  });

  it('renders one pace bar per folded subscription window (#218)', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
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
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const bars = el.querySelectorAll('[data-runner="rn_paced"] [data-testid="runner-pace-bar"]');
    expect(bars).toHaveLength(2);
    expect([...bars].map((b) => b.getAttribute('data-pace-window'))).toEqual(['5h', '7d']);
    expect(bars[0].querySelector('[data-testid="pace-bar-utilization"] .fill')?.getAttribute('style')).toContain(
      'width: 40%',
    );
    expect(bars[0].querySelector('[data-testid="pace-bar-elapsed"] .fill')?.getAttribute('style')).toContain(
      'width: 20%',
    );
  });

  it('renders no subscription-usage component when the row has no declared subscriptions (#218)', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [row('rn_unsampled')]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-runner="rn_unsampled"] [data-testid="runner-subscription-groups"]')).toBeNull();
    expect(el.querySelector('[data-runner="rn_unsampled"] [data-testid="runner-pace-bar"]')).toBeNull();
  });

  it('reports no usage windows for a subscription with a sampled empty window list', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_empty_sample', {
        subscriptionPaces: [
          {
            slug: 'anthropic-default',
            name: 'Anthropic (default)',
            paceBars: [],
            condition: null,
            sampledAt: NOW,
            refreshedLabel: 'refreshed 0s ago',
            freshness: 'fresh',
            missReason: null,
          },
        ],
      }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const report = el.querySelector('[data-testid="subscription-pace-group-unsampled"]');
    expect(report?.textContent?.trim()).toBe('NO USAGE WINDOWS REPORTED');
    expect(report?.getAttribute('aria-label')).toBe('Anthropic (default) sample reported no usage windows');
  });

  it('renders per-subscription groups without merging identical window labels (blizzard#478)', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_multi', {
        subscriptionPaces: [
          {
            slug: 'anthropic-default',
            name: 'Anthropic (default)',
            paceBars: [{ window: '5h', utilizationPct: 40, elapsedPct: 20 }],
            condition: null,
            sampledAt: NOW,
            refreshedLabel: 'refreshed 0s ago',
            freshness: 'fresh',
            missReason: null,
          },
          {
            slug: 'anthropic-secondary',
            name: 'Anthropic (secondary)',
            paceBars: [{ window: '5h', utilizationPct: 90, elapsedPct: 55 }],
            condition: null,
            sampledAt: NOW,
            refreshedLabel: 'refreshed 0s ago',
            freshness: 'fresh',
            missReason: null,
          },
        ],
      }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const groupsHost = el.querySelector('[data-runner="rn_multi"] [data-testid="runner-subscription-groups"]');
    expect(groupsHost).not.toBeNull();
    const groups = [...(groupsHost?.querySelectorAll('[data-testid="subscription-pace-group"]') ?? [])];
    expect(groups.map((group) => group.getAttribute('data-subscription-slug'))).toEqual([
      'anthropic-default',
      'anthropic-secondary',
    ]);
    expect(groups.map((group) => group.querySelector('[data-testid="runner-pace-bar"]')?.getAttribute('data-pace-window'))).toEqual([
      '5h',
      '5h',
    ]);
  });

  it('renders one distinct badge per capability for a two-harness runner, and marks an unavailable one distinguishably', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_multi_harness', {
        capabilities: [
          { harness_id: 'claude', version: '1.2.3', tiers: ['sonnet'], default: true, available: true },
          { harness_id: 'codex', version: '4.5.6', tiers: [], default: false, available: false },
        ],
      }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const host = el.querySelector('[data-runner="rn_multi_harness"] [data-testid="runner-capabilities-group"]');
    expect(host).not.toBeNull();
    const badges = host?.querySelectorAll('[data-testid="runner-capability-badge"]') ?? [];
    expect(badges).toHaveLength(2);
    expect(host?.querySelector('[data-harness-id="claude"]')?.getAttribute('data-available')).toBe('true');
    expect(host?.querySelector('[data-harness-id="codex"]')?.getAttribute('data-available')).toBe('false');
  });

  it('renders one gate badge per node name a runner imposes, and nothing for a runner with none', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_gated', { gates: ['build', 'review'] }),
      row('rn_ungated', { gates: [] }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const gated = el.querySelector('[data-runner="rn_gated"] [data-testid="runner-gates-group"]');
    expect([...(gated?.querySelectorAll('[data-testid="runner-gate-badge"]') ?? [])].map((b) => b.textContent?.trim())).toEqual([
      'build',
      'review',
    ]);
    expect(el.querySelector('[data-runner="rn_ungated"] [data-testid="runner-gates"]')).toBeNull();
  });

  it('renders a labelled empty branch for a runner with no reported capabilities', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [row('rn_no_capabilities', { capabilities: [] })]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const host = el.querySelector('[data-runner="rn_no_capabilities"] [data-testid="runner-capabilities-group"]');
    expect(host?.querySelector('[data-testid="runner-capability-badge"]')).toBeNull();
    expect(host?.querySelector('[data-testid="runner-capabilities-empty"]')?.textContent?.trim()).toBe(
      'NO CAPABILITIES REPORTED',
    );
  });

  it('emits togglePause with the row when the pause/resume button is activated', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
    const target = row('rn_online');
    fixture.componentRef.setInput('rows', [target]);
    fixture.componentRef.setInput('canPause', true);
    let emitted: RunnerRow | undefined;
    fixture.componentInstance.togglePause.subscribe((r) => (emitted = r));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="runner-toggle"]')?.click();
    expect(emitted).toEqual(target);
  });

  it('withholds the hub pause/resume brake when canPause is false (contributor — #93), keeping the row and its paused badges', async () => {
    // `runner:pause` is admin-tier: a contributor still reads the registry and its live
    // liveness/paused state, but is not offered the brake it could only 403 on.
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [row('rn_paused', { hub_paused: true })]);
    fixture.componentRef.setInput('canPause', false);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="runner-toggle"]')).toBeNull();
    // The registry row and its informational paused badge (a `fleet:view` read) stay.
    expect(el.querySelector('[data-runner="rn_paused"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="runner-hub-paused"]')).not.toBeNull();
  });

  it('defaults to withholding the brake when canPause is unset', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [row('rn_online')]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="runner-toggle"]')).toBeNull();
  });

  describe('pause/resume pending state (Part A — per-row mutation scope)', () => {
    it("disables only the pending row's own toggle, off pendingRunnerIds — a sibling stays enabled", async () => {
      const fixture = TestBed.createComponent(RunnerPanelView);
      fixture.componentRef.setInput('state', 'ready');
      fixture.componentRef.setInput('rows', [row('rn_a'), row('rn_b')]);
      fixture.componentRef.setInput('canPause', true);
      fixture.componentRef.setInput('pendingRunnerIds', ['rn_a']);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector<HTMLButtonElement>('[data-runner="rn_a"] [data-testid="runner-toggle"]')?.disabled).toBe(
        true,
      );
      expect(el.querySelector<HTMLButtonElement>('[data-runner="rn_b"] [data-testid="runner-toggle"]')?.disabled).toBe(
        false,
      );
    });

    it('leaves every toggle enabled when pendingRunnerIds is empty', async () => {
      const fixture = TestBed.createComponent(RunnerPanelView);
      fixture.componentRef.setInput('state', 'ready');
      fixture.componentRef.setInput('rows', [row('rn_a')]);
      fixture.componentRef.setInput('canPause', true);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector<HTMLButtonElement>('[data-testid="runner-toggle"]')?.disabled).toBe(false);
    });
  });

  describe('actionError (Part A — visible failure slot)', () => {
    it('renders a carried actionError near the registry', async () => {
      const fixture = TestBed.createComponent(RunnerPanelView);
      fixture.componentRef.setInput('state', 'ready');
      fixture.componentRef.setInput('rows', []);
      fixture.componentRef.setInput('actionError', 'Pause failed.');
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="runner-action-error"]')?.textContent).toBe('Pause failed.');
    });

    it('renders nothing when actionError is null', async () => {
      const fixture = TestBed.createComponent(RunnerPanelView);
      fixture.componentRef.setInput('state', 'ready');
      fixture.componentRef.setInput('rows', []);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="runner-action-error"]')).toBeNull();
    });
  });

  describe('seenLabel (bzh:utc-instants)', () => {
    const REF = Date.parse('2026-07-16T12:00:00.000Z');

    it('reads a fresh heartbeat as "seen Ns ago"', async () => {
      const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
      fixture.componentRef.setInput('rows', [row('r1', { last_seen_at: '2026-07-16T11:59:55.000Z', nowMs: REF })]);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;
      expect(el.querySelector('[data-testid="runner-seen"]')?.textContent).toBe('seen 5s ago');
    });

    it('does not render a confident 0s for a stamp hours in the future — falls through to online/offline', async () => {
      const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
      fixture.componentRef.setInput('rows', [
        row('r1', { last_seen_at: '2026-07-16T17:00:00.000Z', online: false, nowMs: REF }),
      ]);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;
      expect(el.querySelector('[data-testid="runner-seen"]')?.textContent).toBe('offline');
    });
  });
});

describe('RunnerPanelView host cards', () => {
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
    heldChunks: new Map(),
    workspaces: [
      { workspaceId: 'ws-1', capacity: 3, inUse: 2, flag: null, environments: [] },
      { workspaceId: 'ws-2', capacity: null, inUse: 0, flag: null, environments: [] },
    ],
    subscriptionPaces: [
      {
        slug: 'anthropic',
        name: 'Anthropic',
        paceBars: [],
        condition: null,
        sampledAt: null,
        refreshedLabel: null,
        freshness: null,
        missReason: null,
        spenders: ['rA-name'],
      },
    ],
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [RunnerPanelView],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  async function renderCards(rows: readonly RunnerRow[], cards: readonly HostCard[]): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', rows);
    fixture.componentRef.setInput('cards', cards);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('renders one card holding both of its runners, its workspaces, and each subscription once with its spenders', async () => {
    const el = await renderCards(
      [
        row('rA', { host_id: 'h1', env_capacity: 2, bindingLines: [{ project: 'Default', workspaceId: 'ws-1', agents: 2, agentsInUse: 1, unreported: false }], subscriptionName: 'Anthropic' }),
        row('rB', { host_id: 'h1', subscription_unrostered: true, subscriptionName: 'zzz' }),
        row('rC'),
      ],
      [CARD],
    );

    const cards = el.querySelectorAll('[data-testid="host-card"]');
    expect(cards).toHaveLength(1);
    const card = cards[0];
    expect(card.querySelector('[data-testid="host-name"]')?.textContent?.trim()).toBe('build-box');
    expect(card.querySelector('[data-testid="host-report"]')?.textContent?.trim()).toBe('seen 4s ago');
    expect([...card.querySelectorAll('[data-testid="runner"]')].map((li) => li.getAttribute('data-runner'))).toEqual(['rA', 'rB']);
    expect([...card.querySelectorAll('[data-testid="workspace-row"]')].map((ws) => ws.getAttribute('data-workspace-id'))).toEqual(['ws-1', 'ws-2']);
    expect(card.querySelector('[data-testid="workspace-in-use"]')?.textContent?.trim()).toBe('2 / 3 in use');
    expect(card.querySelectorAll('[data-testid="host-subscriptions"]')).toHaveLength(1);
    expect(card.querySelector('[data-testid="host-subscriptions"]')?.textContent).toContain('rA-name');

    // A hosted row shows its bindings and subscription in place of its workspace line and slot bar.
    const rA = card.querySelector('[data-runner="rA"]');
    expect(rA?.querySelector('[data-testid="runner-binding-agents"]')?.textContent?.trim()).toBe('1 / 2 agents');
    expect(rA?.querySelector('[data-testid="runner-subscription-name"]')?.textContent?.trim()).toBe('Anthropic');
    expect(rA?.querySelector('[data-testid="runner-slot-bar"]')).toBeNull();
    expect(card.querySelector('[data-runner="rB"] [data-testid="runner-subscription-unrostered"]')).not.toBeNull();
  });

  it('renders a host-less runner under its own heading, below the cards', async () => {
    const el = await renderCards([row('rA', { host_id: 'h1' }), row('rC', { env_capacity: 2 })], [CARD]);

    const heading = el.querySelector('[data-testid="hostless-heading"]');
    expect(heading?.textContent?.trim()).toBe('No host');
    const hostless = el.querySelector('[data-testid="runner-list"]');
    expect([...(hostless?.querySelectorAll('[data-testid="runner"]') ?? [])].map((li) => li.getAttribute('data-runner'))).toEqual(['rC']);
    expect(hostless?.querySelector('[data-testid="runner-served"]')).not.toBeNull();
    expect(el.querySelector('[data-runner="rC"] [data-testid="runner-slot-bar"]')).not.toBeNull();
  });

  it('shows the host-less heading when no host is registered', async () => {
    const el = await renderCards([row('rC')], []);

    expect(el.querySelector('[data-testid="host-card"]')).toBeNull();
    expect(el.querySelector('[data-testid="hostless-heading"]')).not.toBeNull();
  });

  it('renders a host holding no runner as a card with no rows', async () => {
    const el = await renderCards([], [CARD]);

    expect(el.querySelectorAll('[data-testid="host-card"]')).toHaveLength(1);
    expect(el.querySelector('[data-testid="hostless-heading"]')).toBeNull();
  });
});
