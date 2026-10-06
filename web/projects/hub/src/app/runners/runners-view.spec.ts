import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import type { RunnerRow } from './runner-rows';

import { RunnersView } from './runners-view';

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
  ...over,
});

describe('RunnersView (mobile Fleet screen)', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [RunnersView],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders each row with its liveness, workspace, and claims — off plain inputs alone', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_online', { claims: [{ chunkId: 'ch_01', shortId: 'C-01', node: 'build', status: 'running' }] }),
      row('rn_paused', { hub_paused: true }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="mobile-fleet-runner"]')).toHaveLength(2);
    expect(el.querySelector('[data-runner="rn_online"]')?.getAttribute('data-online')).toBe('true');
    expect(el.querySelector('[data-runner="rn_online"] [data-testid="mobile-fleet-runner-workspace"]')?.textContent).toBe(
      'ws_a',
    );
    const claims = el.querySelectorAll('[data-runner="rn_online"] [data-testid="mobile-fleet-runner-claim"]');
    expect(claims).toHaveLength(1);
    expect(claims[0].textContent).toContain('build');
    expect(claims[0].textContent).toContain('running');
    expect(el.querySelector('[data-runner="rn_online"] [data-testid="mobile-fleet-runner-idle"]')).toBeNull();
    expect(el.querySelector('[data-runner="rn_paused"] [data-testid="mobile-fleet-runner-hub-paused"]')).not.toBeNull();
  });

  it('names a runner with no current work as idle rather than an empty claims list', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [row('rn_idle')]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-runner="rn_idle"] [data-testid="mobile-fleet-runner-idle"]')).not.toBeNull();
    expect(el.querySelector('[data-runner="rn_idle"] [data-testid="mobile-fleet-runner-claims"]')).toBeNull();
  });

  it('shows the empty state for no rows once loaded', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'empty');
    fixture.componentRef.setInput('rows', []);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="mobile-fleet-empty"]')).not.toBeNull();
  });

  it('withholds the empty copy while the registry read is pending', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'loading');
    fixture.componentRef.setInput('rows', []);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="mobile-fleet-loading"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="mobile-fleet-empty"]')).toBeNull();
  });

  it('shows an error state when the registry read fails', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'error');
    fixture.componentRef.setInput('rows', []);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="mobile-fleet-error"]')).not.toBeNull();
  });

  it('names a spend-ceiling escalation reason on the locally-paused badge', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_ceiling', { locally_paused: true, locally_paused_reason: 'spend ceiling $5.00 reached' }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="mobile-fleet-runner-locally-paused"]')?.getAttribute('title')).toBe(
      'spend ceiling $5.00 reached',
    );
  });

  it('renders a slot bar for a reported capacity, and omits it when null', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_cap', { env_capacity: 4, used: 2 }),
      row('rn_nocap', { env_capacity: null, used: 0 }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const bar = el.querySelector('[data-runner="rn_cap"] [data-testid="mobile-fleet-runner-slot-bar"]');
    expect(bar).not.toBeNull();
    expect(bar?.querySelectorAll('.cell.on')).toHaveLength(2);
    expect(el.querySelector('[data-runner="rn_nocap"] [data-testid="mobile-fleet-runner-slot-bar"]')).toBeNull();
  });

  const FRESH_PACE = { sampledAt: NOW, refreshedLabel: 'refreshed 0s ago', freshness: 'fresh' as const, missReason: null };

  it('renders one pace bar per folded subscription window', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_paced', {
        subscriptionPaces: [
          {
            slug: 'anthropic-default',
            name: 'Anthropic (default)',
            condition: null,
            paceBars: [
              { window: '5h', utilizationPct: 40, elapsedPct: 20 },
              { window: '7d', utilizationPct: 70, elapsedPct: 55 },
            ],
            ...FRESH_PACE,
          },
        ],
      }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const bars = el.querySelectorAll('[data-testid="runner-pace-bar"]');
    expect(bars).toHaveLength(2);
    expect([...bars].map((b) => b.getAttribute('data-pace-window'))).toEqual(['5h', '7d']);
  });

  it('renders per-subscription groups without merging identical window labels', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_multi', {
        subscriptionPaces: [
          {
            slug: 'anthropic-default',
            name: 'Anthropic (default)',
            condition: null,
            paceBars: [{ window: '5h', utilizationPct: 40, elapsedPct: 20 }],
            ...FRESH_PACE,
          },
          {
            slug: 'anthropic-secondary',
            name: 'Anthropic (secondary)',
            condition: null,
            paceBars: [{ window: '5h', utilizationPct: 90, elapsedPct: 55 }],
            ...FRESH_PACE,
          },
        ],
      }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const subs = el.querySelector('[data-runner="rn_multi"] [data-testid="mobile-fleet-runner-subscriptions"]');
    expect(subs).not.toBeNull();
    const groups = [...(subs?.querySelectorAll('[data-subscription-slug]') ?? [])];
    expect(groups.map((group) => group.getAttribute('data-subscription-slug'))).toEqual([
      'anthropic-default',
      'anthropic-secondary',
    ]);
    expect(
      groups.map((group) => group.querySelector('[data-testid="runner-pace-bar"]')?.getAttribute('data-pace-window')),
    ).toEqual(['5h', '5h']);
  });

  it('renders no subscription-usage component when the row has no declared subscriptions', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [row('rn_unsampled')]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-runner="rn_unsampled"] [data-testid="mobile-fleet-runner-subscriptions"]')).toBeNull();
    expect(el.querySelector('[data-runner="rn_unsampled"] [data-testid="runner-pace-bar"]')).toBeNull();
  });

  it('reports no usage windows for a subscription with a sampled empty window list', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_unsampled', {
        subscriptionPaces: [
          { slug: 'anthropic-default', name: 'Anthropic (default)', condition: null, paceBars: [], ...FRESH_PACE },
        ],
      }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const unsampled = el.querySelector('[data-testid="subscription-pace-group-unsampled"]');
    expect(unsampled?.textContent?.trim()).toBe('NO USAGE WINDOWS REPORTED');
    expect(unsampled?.getAttribute('aria-label')).toBe('Anthropic (default) sample reported no usage windows');
  });

  it('reads "no sample yet" for a declared, never-sampled slug, naming its miss reason', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_never', {
        subscriptionPaces: [
          {
            slug: 'probe',
            name: 'Probe',
            condition: null,
            paceBars: [],
            sampledAt: null,
            refreshedLabel: null,
            freshness: null,
            missReason: 'endpoint_unreachable',
          },
        ],
      }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const noSample = el.querySelector('[data-testid="subscription-pace-group-no-sample"]');
    expect(noSample?.textContent?.trim()).toBe('NO SAMPLE YET — endpoint_unreachable');
  });

  it('renders the aging/stale refreshed label with its own data-freshness attribute', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_aging', {
        subscriptionPaces: [
          {
            slug: 'anthropic-default',
            name: 'Anthropic (default)',
            condition: null,
            paceBars: [{ window: '5h', utilizationPct: 40, elapsedPct: 60 }],
            sampledAt: NOW,
            refreshedLabel: 'refreshed 30m ago',
            freshness: 'aging',
            missReason: null,
          },
        ],
      }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const refreshed = el.querySelector('[data-testid="subscription-pace-group-refreshed"]');
    expect(refreshed?.textContent?.trim()).toBe('refreshed 30m ago');
    expect(refreshed?.getAttribute('data-freshness')).toBe('aging');
  });

  it('emits togglePause with the row when the pause/resume button is activated', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'ready');
    const target = row('rn_online');
    fixture.componentRef.setInput('rows', [target]);
    fixture.componentRef.setInput('canPause', true);
    let emitted: RunnerRow | undefined;
    fixture.componentInstance.togglePause.subscribe((r) => (emitted = r));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="mobile-fleet-runner-toggle"]')?.click();
    expect(emitted).toEqual(target);
  });

  it('withholds the hub pause/resume brake when canPause is false, keeping the row and its paused badges', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [row('rn_paused', { hub_paused: true })]);
    fixture.componentRef.setInput('canPause', false);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="mobile-fleet-runner-toggle"]')).toBeNull();
    expect(el.querySelector('[data-runner="rn_paused"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="mobile-fleet-runner-hub-paused"]')).not.toBeNull();
  });

  it('defaults to withholding the brake when canPause is unset', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [row('rn_online')]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="mobile-fleet-runner-toggle"]')).toBeNull();
  });

  // --- Pending disable + inline error (`bzh:frontend-pending-override`) ----------

  it("disables a row's own toggle while its runner id is in pendingRunnerIds, re-enabling once cleared", async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [row('rn_online'), row('rn_paused', { hub_paused: true })]);
    fixture.componentRef.setInput('canPause', true);
    fixture.componentRef.setInput('pendingRunnerIds', ['rn_online']);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const toggle = (id: string) =>
      el.querySelector<HTMLButtonElement>(`[data-runner="${id}"] [data-testid="mobile-fleet-runner-toggle"]`);

    // The row named in pendingRunnerIds disables…
    expect(toggle('rn_online')?.disabled).toBe(true);
    // …but a sibling row not in the list stays clickable.
    expect(toggle('rn_paused')?.disabled).toBe(false);

    fixture.componentRef.setInput('pendingRunnerIds', []);
    await fixture.whenStable();

    expect(toggle('rn_online')?.disabled).toBe(false);
  });

  it('renders actionError as a visible inline notice, and nothing when null', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [row('rn_online')]);
    fixture.componentRef.setInput('actionError', 'Pause failed.');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="mobile-fleet-action-error"]')?.textContent).toBe('Pause failed.');

    fixture.componentRef.setInput('actionError', null);
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="mobile-fleet-action-error"]')).toBeNull();
  });

  describe('the rendered seen label (bzh:utc-instants)', () => {
    const REF = Date.parse('2026-07-16T12:00:00.000Z');

    it('reads a fresh heartbeat as "seen Ns ago"', async () => {
      const fixture = TestBed.createComponent(RunnersView);
      fixture.componentRef.setInput('state', 'ready');
      fixture.componentRef.setInput('rows', [row('r1', { last_seen_at: '2026-07-16T11:59:55.000Z', nowMs: REF })]);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;
      expect(el.querySelector('[data-testid="mobile-fleet-runner-seen"]')?.textContent).toBe('seen 5s ago');
    });
  });

  it('marks a retired runner with when and by whom', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_gone', { retired: true, retired_at: '2026-09-28T00:00:00Z', retired_by: 'op' }),
      row('rn_live'),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const badge = el.querySelector('[data-runner="rn_gone"] [data-testid="mobile-fleet-runner-retired"]');
    expect(badge?.textContent).toContain('op');
    expect(badge?.getAttribute('title')).toBe('Retired 2026-09-28T00:00:00Z by op');
    expect(el.querySelector('[data-runner="rn_live"] [data-testid="mobile-fleet-runner-retired"]')).toBeNull();
  });

  it('names each runner beside its full id, and marks one that never connected', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_a', { runner_name: 'r-claude' }),
      row('rn_b', {
        runner_name: 'r-claude',
        connection: 'never_connected',
        online: false,
        workspace_id: null,
        registered_at: null,
        last_seen_at: null,
      }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    for (const id of ['rn_a', 'rn_b']) {
      expect(el.querySelector(`[data-runner="${id}"] [data-testid="mobile-fleet-runner-name"]`)?.textContent).toBe('r-claude');
      expect(el.querySelector(`[data-runner="${id}"] [data-testid="mobile-fleet-runner-id"]`)?.textContent).toBe(id);
    }
    const fresh = el.querySelector('[data-runner="rn_b"]') as HTMLElement;
    expect(fresh.classList.contains('offline')).toBe(false);
    expect(fresh.querySelector('[data-testid="mobile-fleet-runner-never-connected"]')?.textContent).toContain('NEVER CONNECTED');
    expect(fresh.querySelector('[data-testid="mobile-fleet-runner-seen"]')?.textContent).toBe('never connected');
    expect(fresh.querySelector('[data-testid="mobile-fleet-runner-workspace"]')?.textContent).toBe('—');
    expect(el.querySelector('[data-runner="rn_a"] [data-testid="mobile-fleet-runner-never-connected"]')).toBeNull();
  });

  it('renders the show-retired chip reflecting the flag and emits its toggle', async () => {
    const fixture = TestBed.createComponent(RunnersView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', []);
    const toggled = vi.fn();
    fixture.componentInstance.toggleShowRetired.subscribe(toggled);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const chip = el.querySelector('[data-testid="mobile-fleet-show-retired"]') as HTMLElement;
    expect(chip).not.toBeNull();
    chip.click();
    expect(toggled).toHaveBeenCalledTimes(1);
  });
});
