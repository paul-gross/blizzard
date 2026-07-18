import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { runnerApi } from 'fleet';
import { settle } from 'fleet/testing';
import { vi } from 'vitest';

import { AgentRow } from './agent-row';

const REF = Date.parse('2026-07-16T12:00:00.000Z');

function lease(overrides: Partial<runnerApi.LeaseView> = {}): runnerApi.LeaseView {
  return {
    lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
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
    closed_at: null,
    closure_reason: null,
    ...overrides,
  };
}

/**
 * The lease row is presentational since the machine-panel redesign — the PM
 * title enrichment moved to `ChunkRow`, so no TanStack provider or stubbed
 * transport is needed; the row renders its lease input alone.
 */
async function render(agent: runnerApi.LeaseView): Promise<HTMLElement> {
  await TestBed.configureTestingModule({
    imports: [AgentRow],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(AgentRow);
  fixture.componentRef.setInput('agent', agent);
  await settle(fixture);
  return fixture.nativeElement as HTMLElement;
}

describe('AgentRow', () => {
  beforeEach(() => {
    vi.spyOn(Date, 'now').mockReturnValue(REF);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders compact refs for the lease and chunk while data-lease-id keeps the full id', async () => {
    const el = await render(lease());

    const row = el.querySelector('[data-testid="agent-row"]');
    // The stable e2e hook stays the raw id (`bzh:sweep-release-only-tiers`);
    // only the *rendered* name compacts (compactRef — the app-wide mechanism).
    expect(row?.getAttribute('data-lease-id')).toBe('lease_01KXKVVF1J3D6H6VYZ3XYNZPRR');
    expect(row?.textContent).toContain('L-ZPRR');
    expect(row?.textContent).toContain('C-3YJ9');
    expect(row?.textContent).not.toContain('lease_01KXKVVF1J3D6H6VYZ3XYNZPRR');
    expect(row?.textContent).toContain('epoch 2');
  });

  it('renders node, env, pid, and session on the second line', async () => {
    const el = await render(lease());

    const l2 = el.querySelector('.l2')?.textContent ?? '';
    expect(l2).toContain('build');
    expect(l2).toContain('beta');
    expect(l2).toContain('4821');
    expect(l2).toContain('sess-77');
  });

  it('renders the server-derived state as the right-aligned chip', async () => {
    const el = await render(lease({ state: 'parked' }));

    const chip = el.querySelector('[data-testid="agent-state"]');
    expect(chip?.textContent?.trim()).toBe('PARKED');
    expect(chip?.classList.contains('st-parked')).toBe(true);
  });

  it('carries a heartbeat freshness bar fed by last_heartbeat_at', async () => {
    const el = await render(lease());

    const fill = el.querySelector('[data-testid="hb-fill"]');
    expect(fill).not.toBeNull();
    // -34s old under a 1h log drain: partially drained, nowhere near empty.
    const percent = Number(fill?.getAttribute('data-hb-percent'));
    expect(percent).toBeGreaterThan(0);
    expect(percent).toBeLessThan(100);
    expect(el.querySelector('[data-testid="hb-age"]')?.textContent).toContain('-34s');
  });

  it('colors the bar red for a server-derived stale lease', async () => {
    const el = await render(lease({ state: 'stale', last_heartbeat_at: '2026-07-16T09:00:00.000Z' }));

    expect(el.querySelector('[data-testid="hb-fill"]')?.classList.contains('stale')).toBe(true);
  });

  describe('selection', () => {
    async function renderRow(agent: runnerApi.LeaseView) {
      await TestBed.configureTestingModule({
        imports: [AgentRow],
        providers: [provideZonelessChangeDetection()],
      }).compileComponents();
      const fixture = TestBed.createComponent(AgentRow);
      fixture.componentRef.setInput('agent', agent);
      await settle(fixture);
      return fixture;
    }

    it('is a keyboard-reachable button by role', async () => {
      const el = await render(lease());
      const row = el.querySelector('[data-testid="agent-row"]');
      expect(row?.getAttribute('role')).toBe('button');
      expect(row?.getAttribute('tabindex')).toBe('0');
    });

    it('emits selectLease with the full lease_id on click', async () => {
      const fixture = await renderRow(lease());
      const emitted: string[] = [];
      fixture.componentInstance.selectLease.subscribe((id: string) => emitted.push(id));

      (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="agent-row"]')?.click();

      expect(emitted).toEqual(['lease_01KXKVVF1J3D6H6VYZ3XYNZPRR']);
    });

    it('emits selectLease on Enter and Space', async () => {
      const fixture = await renderRow(lease());
      const emitted: string[] = [];
      fixture.componentInstance.selectLease.subscribe((id: string) => emitted.push(id));
      const row = (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="agent-row"]');

      row?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      row?.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));

      expect(emitted).toHaveLength(2);
    });

    it('reflects the selected input as the selected class', async () => {
      const fixture = await renderRow(lease());
      fixture.componentRef.setInput('selected', true);
      await settle(fixture);

      const row = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="agent-row"]');
      expect(row?.classList.contains('selected')).toBe(true);
    });
  });
});
