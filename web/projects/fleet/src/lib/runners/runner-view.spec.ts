import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import type { RunnerRow } from './runner-panel';
import { RunnerPanelView } from './runner-view';

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
    fixture.componentRef.setInput('rows', [
      row('rn_online', { claims: [{ chunkId: 'ch_01', shortId: 'C-01', node: 'build' }] }),
      row('rn_paused', { hub_paused: true }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="runner"]')).toHaveLength(2);
    expect(el.querySelector('[data-runner="rn_online"]')?.getAttribute('data-online')).toBe('true');
    const claims = el.querySelectorAll('[data-runner="rn_online"] [data-testid="runner-claim"]');
    expect(claims).toHaveLength(1);
    expect(claims[0].textContent).toContain('build');
    expect(el.querySelector('[data-runner="rn_paused"] [data-testid="runner-hub-paused"]')).not.toBeNull();
  });

  it('shows the empty state for no rows', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('rows', []);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="runners-empty"]')).not.toBeNull();
  });

  it('names a spend-ceiling escalation reason on the locally-paused badge (#61)', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    fixture.componentRef.setInput('rows', [
      row('rn_ceiling', { locally_paused: true, locally_paused_reason: 'spend ceiling $5.00 reached' }),
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="runner-locally-paused"]')?.getAttribute('title')).toBe(
      'spend ceiling $5.00 reached',
    );
  });

  it('emits togglePause with the row when the pause/resume button is activated', async () => {
    const fixture = TestBed.createComponent(RunnerPanelView);
    const target = row('rn_online');
    fixture.componentRef.setInput('rows', [target]);
    let emitted: RunnerRow | undefined;
    fixture.componentInstance.togglePause.subscribe((r) => (emitted = r));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="runner-toggle"]')?.click();
    expect(emitted).toEqual(target);
  });

  describe('seenLabel (bzh:utc-instants)', () => {
    const REF = Date.parse('2026-07-16T12:00:00.000Z');

    beforeEach(() => vi.spyOn(Date, 'now').mockReturnValue(REF));
    afterEach(() => vi.restoreAllMocks());

    it('reads a fresh heartbeat as "seen Ns ago"', async () => {
      const fixture = TestBed.createComponent(RunnerPanelView);
      fixture.componentRef.setInput('rows', [row('r1', { last_seen_at: '2026-07-16T11:59:55.000Z' })]);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;
      expect(el.querySelector('[data-testid="runner-seen"]')?.textContent).toBe('seen 5s ago');
    });

    it('does not render a confident 0s for a stamp hours in the future — falls through to online/offline', async () => {
      const fixture = TestBed.createComponent(RunnerPanelView);
      fixture.componentRef.setInput('rows', [
        row('r1', { last_seen_at: '2026-07-16T17:00:00.000Z', online: false }),
      ]);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;
      expect(el.querySelector('[data-testid="runner-seen"]')?.textContent).toBe('offline');
    });
  });
});
