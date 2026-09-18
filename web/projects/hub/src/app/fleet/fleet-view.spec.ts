import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import type { RunnerRow } from 'fleet';

import { FleetView } from './fleet-view';

const NOW = new Date().toISOString();

const row = (id: string, over: Partial<RunnerRow> = {}): RunnerRow => ({
  runner_id: id,
  workspace_id: 'ws_a',
  registered_at: NOW,
  last_seen_at: NOW,
  online: true,
  hub_paused: false,
  locally_paused: false,
  claims: [],
  used: 0,
  paceBars: [],
  subscriptionPaces: [],
  ...over,
});

describe('FleetView (mobile Fleet screen)', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [FleetView],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders each row with its liveness, workspace, and claims — off plain inputs alone', async () => {
    const fixture = TestBed.createComponent(FleetView);
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
    const fixture = TestBed.createComponent(FleetView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [row('rn_idle')]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-runner="rn_idle"] [data-testid="mobile-fleet-runner-idle"]')).not.toBeNull();
    expect(el.querySelector('[data-runner="rn_idle"] [data-testid="mobile-fleet-runner-claims"]')).toBeNull();
  });

  it('shows the empty state for no rows once loaded', async () => {
    const fixture = TestBed.createComponent(FleetView);
    fixture.componentRef.setInput('state', 'empty');
    fixture.componentRef.setInput('rows', []);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="mobile-fleet-empty"]')).not.toBeNull();
  });

  it('withholds the empty copy while the registry read is pending', async () => {
    const fixture = TestBed.createComponent(FleetView);
    fixture.componentRef.setInput('state', 'loading');
    fixture.componentRef.setInput('rows', []);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="mobile-fleet-loading"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="mobile-fleet-empty"]')).toBeNull();
  });

  it('shows an error state when the registry read fails', async () => {
    const fixture = TestBed.createComponent(FleetView);
    fixture.componentRef.setInput('state', 'error');
    fixture.componentRef.setInput('rows', []);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="mobile-fleet-error"]')).not.toBeNull();
  });

  it('names a spend-ceiling escalation reason on the locally-paused badge', async () => {
    const fixture = TestBed.createComponent(FleetView);
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
    const fixture = TestBed.createComponent(FleetView);
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

  it('renders one pace bar per folded window', async () => {
    const fixture = TestBed.createComponent(FleetView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_paced', {
        paceBars: [
          { window: '5h', utilizationPct: 40, elapsedPct: 20 },
          { window: '7d', utilizationPct: 70, elapsedPct: 55 },
        ],
      }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const bars = el.querySelectorAll('[data-testid="mobile-fleet-runner-pace-bar"]');
    expect(bars).toHaveLength(2);
    expect([...bars].map((b) => b.getAttribute('data-pace-window'))).toEqual(['5h', '7d']);
  });

  it('renders per-subscription groups instead of the flat loop when the row carries subscriptionPaces', async () => {
    const fixture = TestBed.createComponent(FleetView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_multi', {
        paceBars: [{ window: '5h', utilizationPct: 40, elapsedPct: 20 }],
        subscriptionPaces: [
          { slug: 'anthropic-default', name: 'Anthropic (default)', paceBars: [{ window: '5h', utilizationPct: 40, elapsedPct: 20 }] },
          { slug: 'anthropic-secondary', name: 'Anthropic (secondary)', paceBars: [{ window: '5h', utilizationPct: 90, elapsedPct: 55 }] },
        ],
      }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const subs = el.querySelector('[data-runner="rn_multi"] [data-testid="mobile-fleet-runner-subscriptions"]');
    expect(subs).not.toBeNull();
    expect(subs?.querySelectorAll('[data-testid="mobile-fleet-runner-subscription-name"]')).toHaveLength(2);
    expect(el.querySelector('[data-runner="rn_multi"] [data-testid="mobile-fleet-runner-pace-bars"]')).toBeNull();
  });

  it('renders the not-yet-sampled label for a declared subscription with no folded pace bars', async () => {
    const fixture = TestBed.createComponent(FleetView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [
      row('rn_unsampled', {
        subscriptionPaces: [{ slug: 'anthropic-default', name: 'Anthropic (default)', paceBars: [] }],
      }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const unsampled = el.querySelector('[data-testid="mobile-fleet-runner-subscription-unsampled"]');
    expect(unsampled?.textContent).toBe('NOT YET SAMPLED');
    expect(unsampled?.getAttribute('aria-label')).toBe('Anthropic (default) usage not yet sampled');
  });

  it('emits togglePause with the row when the pause/resume button is activated', async () => {
    const fixture = TestBed.createComponent(FleetView);
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
    const fixture = TestBed.createComponent(FleetView);
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
    const fixture = TestBed.createComponent(FleetView);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('rows', [row('rn_online')]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="mobile-fleet-runner-toggle"]')).toBeNull();
  });

  // --- Pending disable + inline error (`bzh:frontend-pending-override`) ----------

  it("disables a row's own toggle while its runner id is in pendingRunnerIds, re-enabling once cleared", async () => {
    const fixture = TestBed.createComponent(FleetView);
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
    const fixture = TestBed.createComponent(FleetView);
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

    beforeEach(() => vi.spyOn(Date, 'now').mockReturnValue(REF));
    afterEach(() => vi.restoreAllMocks());

    it('reads a fresh heartbeat as "seen Ns ago"', async () => {
      const fixture = TestBed.createComponent(FleetView);
      fixture.componentRef.setInput('state', 'ready');
      fixture.componentRef.setInput('rows', [row('r1', { last_seen_at: '2026-07-16T11:59:55.000Z' })]);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;
      expect(el.querySelector('[data-testid="mobile-fleet-runner-seen"]')?.textContent).toBe('seen 5s ago');
    });
  });
});
