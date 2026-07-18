import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { settle } from 'fleet/testing';
import { vi } from 'vitest';

import { HeartbeatFreshness } from './heartbeat-freshness';

const REF = Date.parse('2026-07-16T12:00:00.000Z');

async function render(lastHeartbeatAt: string | null, stale = false): Promise<HTMLElement> {
  await TestBed.configureTestingModule({
    imports: [HeartbeatFreshness],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(HeartbeatFreshness);
  fixture.componentRef.setInput('lastHeartbeatAt', lastHeartbeatAt);
  fixture.componentRef.setInput('stale', stale);
  await settle(fixture);
  return fixture.nativeElement as HTMLElement;
}

function percentOf(el: HTMLElement): number {
  return Number(el.querySelector('[data-testid="hb-fill"]')?.getAttribute('data-hb-percent'));
}

describe('HeartbeatFreshness', () => {
  beforeEach(() => {
    vi.spyOn(Date, 'now').mockReturnValue(REF);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('reads 100% the instant a beat lands', async () => {
    const el = await render('2026-07-16T12:00:00.000Z');
    expect(percentOf(el)).toBe(100);
    expect(el.querySelector('[data-testid="hb-age"]')?.textContent).toContain('-0s');
  });

  it('drains logarithmically — visibly down within the first minute, not pinned at ~100%', async () => {
    // A linear drain over the 1h reap threshold would leave a 60s-old beat at
    // ~98%; the log drain puts it near half — the operator can see it move.
    const el = await render('2026-07-16T11:59:00.000Z');
    const percent = percentOf(el);
    expect(percent).toBeLessThan(70);
    expect(percent).toBeGreaterThan(30);
  });

  it('reaches 0% at the reap staleness threshold', async () => {
    const el = await render('2026-07-16T11:00:00.000Z'); // exactly 1h old
    expect(percentOf(el)).toBe(0);
  });

  it('renders an empty bar and — for a lease with no heartbeat fact yet', async () => {
    const el = await render(null);
    expect(percentOf(el)).toBe(0);
    expect(el.querySelector('[data-testid="hb-age"]')?.textContent).toContain('—');
  });

  it('floors benign skew (a beat slightly in the future) at 100% rather than going negative', async () => {
    const el = await render('2026-07-16T12:00:30.000Z'); // 30s ahead, inside tolerance
    expect(percentOf(el)).toBe(100);
  });

  it('renders — for a timestamp beyond the skew tolerance instead of a confident bar', async () => {
    const el = await render('2026-07-16T15:00:00.000Z'); // hours ahead — not skew
    expect(percentOf(el)).toBe(0);
    expect(el.querySelector('[data-testid="hb-age"]')?.textContent).toContain('—');
  });

  it('colors the fill red when the server derived the lease stale', async () => {
    const el = await render('2026-07-16T10:00:00.000Z', true);
    expect(el.querySelector('[data-testid="hb-fill"]')?.classList.contains('stale')).toBe(true);
  });
});
