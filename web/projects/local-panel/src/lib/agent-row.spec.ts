import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { runnerApi } from 'fleet';
import { vi } from 'vitest';

import { AgentRow } from './agent-row';

const REF = Date.parse('2026-07-16T12:00:00.000Z');

function lease(overrides: Partial<runnerApi.LeaseView> = {}): runnerApi.LeaseView {
  return {
    lease_id: 'L-903',
    chunk_id: 'C-125',
    graph_id: 'gr_1',
    node_id: 'nd_build',
    node_name: 'build',
    epoch: 2,
    session_id: 'sess-77',
    pid: 4821,
    environment_id: 'beta',
    workdir: '/ws/beta',
    created_at: '2026-07-16T11:00:00.000Z',
    last_heartbeat_at: '2026-07-16T11:59:26.000Z', // -34s from REF
    state: 'running',
    ...overrides,
  };
}

async function render(agent: runnerApi.LeaseView): Promise<HTMLElement> {
  await TestBed.configureTestingModule({
    imports: [AgentRow],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(AgentRow);
  fixture.componentRef.setInput('agent', agent);
  await fixture.whenStable();
  fixture.detectChanges();
  return fixture.nativeElement as HTMLElement;
}

describe('AgentRow', () => {
  beforeEach(() => {
    vi.spyOn(Date, 'now').mockReturnValue(REF);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders the lease/chunk/epoch identity and the data-lease-id hook (issue #28, shaped for #29)', async () => {
    const el = await render(lease());

    const row = el.querySelector('[data-testid="agent-row"]');
    expect(row?.getAttribute('data-lease-id')).toBe('L-903');
    expect(row?.textContent).toContain('L-903');
    expect(row?.textContent).toContain('C-125');
    expect(row?.textContent).toContain('epoch 2');
  });

  it('renders node, env, pid, and session on the second line — no title (phase 6 is title-free)', async () => {
    const el = await render(lease());

    const l2 = el.querySelector('.l2')?.textContent ?? '';
    expect(l2).toContain('build');
    expect(l2).toContain('beta');
    expect(l2).toContain('4821');
    expect(l2).toContain('sess-77');
  });

  it('has no (select) output and no role="button" — #29\'s territory, not phase 6\'s', () => {
    // Structural guard: the component declares no output named `select`.
    expect(Object.getOwnPropertyNames(AgentRow.prototype)).not.toContain('select');
  });

  it.each([
    ['running', 'st-running'],
    ['stale', 'st-stale'],
    ['parked', 'st-parked'],
    ['spawning', 'st-spawning'],
    ['exited', 'st-exited'],
  ] as const)('renders the %s state with its %s class', async (state, cls) => {
    const el = await render(lease({ state }));
    const stEl = el.querySelector('[data-testid="agent-state"]');
    expect(stEl?.classList.contains(cls)).toBe(true);
    expect(stEl?.textContent?.trim()).toBe(state.toUpperCase());
  });

  describe('heartbeat age', () => {
    it('renders "—" (not "-0s") for a spawning lease with no heartbeat yet', async () => {
      const el = await render(lease({ state: 'spawning', last_heartbeat_at: null, pid: null, session_id: null }));
      expect(el.querySelector('[data-testid="agent-hb-age"]')?.textContent?.trim()).toBe('—');
    });

    it('does not fall back to created_at for a spawning lease', async () => {
      // created_at is hours before REF; if the row fell back to it the age would be
      // a large number, not the honest "—".
      const el = await render(
        lease({ state: 'spawning', last_heartbeat_at: null, created_at: '2026-07-16T09:00:00.000Z' }),
      );
      expect(el.querySelector('[data-testid="agent-hb-age"]')?.textContent?.trim()).toBe('—');
    });

    it('formats a sub-minute age as -Ns', async () => {
      const el = await render(lease({ last_heartbeat_at: '2026-07-16T11:59:26.000Z' })); // -34s
      expect(el.querySelector('[data-testid="agent-hb-age"]')?.textContent?.trim()).toBe('-34s');
    });

    it('formats a sub-hour age as -Nm', async () => {
      const el = await render(lease({ last_heartbeat_at: '2026-07-16T11:48:00.000Z' })); // -12m
      expect(el.querySelector('[data-testid="agent-hb-age"]')?.textContent?.trim()).toBe('-12m');
    });

    it('formats an hour-plus age as -HhMMm', async () => {
      const el = await render(lease({ last_heartbeat_at: '2026-07-16T10:55:56.000Z' })); // -1h04m
      expect(el.querySelector('[data-testid="agent-hb-age"]')?.textContent?.trim()).toBe('-1h04m');
    });

    it('colors a stale lease\'s age --red via the "stale" class, not the state label alone', async () => {
      const el = await render(lease({ state: 'stale', last_heartbeat_at: '2026-07-16T11:00:00.000Z' }));
      const ageEl = el.querySelector('[data-testid="agent-hb-age"]');
      expect(ageEl?.classList.contains('stale')).toBe(true);
      expect(ageEl?.classList.contains('dim')).toBe(false);
    });

    it('dims a parked lease\'s age instead of using the stale color, even though the age keeps growing', async () => {
      // The reap clock is stopped for a parked lease — a large age is expected, not alarming.
      const el = await render(lease({ state: 'parked', last_heartbeat_at: '2026-07-16T08:00:00.000Z' }));
      const ageEl = el.querySelector('[data-testid="agent-hb-age"]');
      expect(ageEl?.classList.contains('dim')).toBe(true);
      expect(ageEl?.classList.contains('stale')).toBe(false);
      expect(ageEl?.textContent?.trim()).toBe('-4h00m');
    });

    it('renders a running lease\'s age undecorated (no stale, no dim)', async () => {
      const el = await render(lease({ state: 'running', last_heartbeat_at: '2026-07-16T11:59:26.000Z' }));
      const ageEl = el.querySelector('[data-testid="agent-hb-age"]');
      expect(ageEl?.classList.contains('stale')).toBe(false);
      expect(ageEl?.classList.contains('dim')).toBe(false);
    });

    // bzh:utc-instants — the bounded-tolerance region agent-row.ts must mirror from
    // runner-strip.ts's seenLabel: a positive age (above), a small negative age
    // (benign browser-vs-hub skew, reads as "-0s"), and a large negative age (not
    // skew — falls through to "—" rather than a confident "-0s").
    it('reads a small browser-vs-hub skew (last_heartbeat_at up to 60s in the future) as "-0s"', async () => {
      const el = await render(lease({ last_heartbeat_at: '2026-07-16T12:00:30.000Z' })); // 30s after REF
      expect(el.querySelector('[data-testid="agent-hb-age"]')?.textContent?.trim()).toBe('-0s');
    });

    it('still reads "-0s" at exactly the 60s tolerance boundary', async () => {
      const el = await render(lease({ last_heartbeat_at: '2026-07-16T12:01:00.000Z' })); // 60s after REF
      expect(el.querySelector('[data-testid="agent-hb-age"]')?.textContent?.trim()).toBe('-0s');
    });

    it('does not render a confident "-0s" for a heartbeat stamp hours in the future — falls through to "—"', async () => {
      // The naive-timestamp bug this guards against: a naive wire stamp on a UTC-5
      // box reads five hours ahead of the true instant (bzh:utc-instants).
      const el = await render(lease({ last_heartbeat_at: '2026-07-16T17:00:00.000Z' })); // 5h after REF
      expect(el.querySelector('[data-testid="agent-hb-age"]')?.textContent?.trim()).toBe('—');
    });
  });
});
