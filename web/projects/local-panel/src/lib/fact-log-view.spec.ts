import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { runnerApi } from 'fleet';
import { vi } from 'vitest';

import { FactLogView } from './fact-log-view';

async function render(facts: readonly runnerApi.FactView[]) {
  await TestBed.configureTestingModule({
    imports: [FactLogView],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(FactLogView);
  fixture.componentRef.setInput('facts', facts);
  fixture.detectChanges();
  await fixture.whenStable();
  return { el: fixture.nativeElement as HTMLElement };
}

describe('FactLogView', () => {
  describe('fact timestamps render in browser-local time (issue #136)', () => {
    // Pin both the zone and "now" so the local-day boundary is deterministic —
    // a bare wall-clock read would make this flaky in CI.
    beforeEach(() => {
      vi.stubEnv('TZ', 'America/New_York');
      vi.setSystemTime(new Date('2026-07-16T15:00:00.000Z')); // 11:00 EDT
    });

    afterEach(() => {
      vi.useRealTimers();
      vi.unstubAllEnvs();
    });

    it("renders today's fact as the local time alone, no day line — no query stub required", async () => {
      const { el } = await render([
        {
          seq: 1,
          kind: 'chunk_claimed',
          created_at: '2026-07-16T11:00:00+00:00', // 07:00 EDT, same local day as "now"
          chunk_id: null,
          lease_id: null,
          acked_at: null,
        },
      ]);

      const row = el.querySelector('[data-testid="fact-row"]');
      expect(row?.querySelector('.t .day')).toBeNull();
      expect(row?.querySelector('.t .time')?.textContent).toBe('07:00:00');
      expect(row?.querySelector('.t')?.getAttribute('title')).toBe('2026/07/16 07:00:00');
    });

    it("renders yesterday's fact as \"Yesterday\" above the local time", async () => {
      const { el } = await render([
        {
          seq: 1,
          kind: 'chunk_claimed',
          created_at: '2026-07-15T23:30:00+00:00', // 19:30 EDT the day before "now"
          chunk_id: null,
          lease_id: null,
          acked_at: null,
        },
      ]);

      const row = el.querySelector('[data-testid="fact-row"]');
      expect(row?.querySelector('.t .day')?.textContent).toBe('Yesterday');
      expect(row?.querySelector('.t .time')?.textContent).toBe('19:30:00');
    });
  });

  it('renders the fact kind plus its correlated chunk/lease compact refs', async () => {
    const { el } = await render([
      {
        seq: 1,
        kind: 'chunk_claimed',
        created_at: '2026-07-16T11:00:00+00:00',
        chunk_id: 'ch_01ABCDEF00000000000000000',
        lease_id: 'lease_01ABCDEF00000000000000000',
        acked_at: null,
      },
    ]);

    const row = el.querySelector('[data-testid="fact-row"]');
    expect(row?.querySelector('.kind')?.textContent).toBe('chunk_claimed');
    expect(row?.querySelectorAll('.ref')).toHaveLength(2);
  });

  it('renders the ack marker only once the hub has acked the seq', async () => {
    const { el } = await render([
      { seq: 1, kind: 'chunk_claimed', created_at: '2026-07-16T11:00:00+00:00', chunk_id: null, lease_id: null, acked_at: null },
      {
        seq: 2,
        kind: 'chunk_claimed',
        created_at: '2026-07-16T11:00:00+00:00',
        chunk_id: null,
        lease_id: null,
        acked_at: '2026-07-16T11:00:01+00:00',
      },
    ]);

    const rows = el.querySelectorAll('[data-testid="fact-row"]');
    expect(rows[0].querySelector('.flush')?.classList.contains('acked')).toBe(false);
    expect(rows[1].querySelector('.flush')?.classList.contains('acked')).toBe(true);
  });
});
