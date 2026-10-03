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

  it('renders 100% for any age at or under the sampling interval, not just the instant a beat lands (blizzard#334 D4)', async () => {
    // Exactly one RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS old (60s) — the bar cannot
    // resolve an age this fine, so it claims no partial drain it cannot back.
    const el = await render('2026-07-16T11:59:00.000Z');
    expect(percentOf(el)).toBe(100);
  });

  it('drains logarithmically once past the sampling interval — ≈78% at the floor, one beat gap past it (blizzard#334 D4)', async () => {
    // 65s old: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS (60s) plus one 5s beat gap —
    // the instant before the next backstop poll would refresh the anchor.
    const el = await render('2026-07-16T11:58:55.000Z');
    const percent = percentOf(el);
    expect(percent).toBeLessThan(85);
    expect(percent).toBeGreaterThan(70);
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

describe('HeartbeatFreshness ticking (issue #178)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(REF);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('drains the bar on the tick alone, with no new lastHeartbeatAt input', async () => {
    await TestBed.configureTestingModule({
      imports: [HeartbeatFreshness],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(HeartbeatFreshness);
    fixture.componentRef.setInput('lastHeartbeatAt', '2026-07-16T12:00:00.000Z');
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(percentOf(el)).toBe(100);

    // Past the sampling interval (60s) plus a beat gap, so the tick alone — with
    // no fresh `lastHeartbeatAt` — drives the age past what the bar renders 100% for.
    vi.setSystemTime(REF + 70_000);
    await vi.advanceTimersByTimeAsync(1000);
    fixture.detectChanges();

    expect(percentOf(el)).toBeLessThan(100);
    expect(el.querySelector('[data-testid="hb-age"]')?.textContent).toContain('-1m');
  });

  it('still resets immediately when a fresh lastHeartbeatAt input lands', async () => {
    await TestBed.configureTestingModule({
      imports: [HeartbeatFreshness],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(HeartbeatFreshness);
    // Past the sampling interval, so the initial render is already draining.
    fixture.componentRef.setInput('lastHeartbeatAt', '2026-07-16T11:58:00.000Z');
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(percentOf(el)).toBeLessThan(100);

    vi.setSystemTime(REF + 60_000);
    fixture.componentRef.setInput('lastHeartbeatAt', '2026-07-16T12:01:00.000Z');
    fixture.detectChanges();

    expect(percentOf(el)).toBe(100);
  });
});
