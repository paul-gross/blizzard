import {
  FRESHNESS_AGING_AFTER_MS,
  FRESHNESS_STALE_AFTER_MS,
  classifySubscriptionFreshness,
  toPaceBars,
  windowElapsedPct,
} from './subscription-pace.model';

describe('windowElapsedPct', () => {
  const FIVE_H_SECONDS = 5 * 60 * 60;

  it('reads ~0 right at the window\'s own start (just reset)', () => {
    const resetsAt = '2026-07-16T17:00:00.000Z';
    const nowMs = Date.parse('2026-07-16T12:00:00.000Z'); // exactly resetsAt - 5h
    expect(windowElapsedPct(nowMs, resetsAt, FIVE_H_SECONDS)).toBe(0);
  });

  it('reads ~100 right at the instant the window resets (about to reset)', () => {
    const resetsAt = '2026-07-16T17:00:00.000Z';
    const nowMs = Date.parse(resetsAt);
    expect(windowElapsedPct(nowMs, resetsAt, FIVE_H_SECONDS)).toBe(100);
  });

  it('reads the midpoint at half the window elapsed', () => {
    const resetsAt = '2026-07-16T17:00:00.000Z';
    const nowMs = Date.parse('2026-07-16T14:30:00.000Z'); // resetsAt - 2.5h
    expect(windowElapsedPct(nowMs, resetsAt, FIVE_H_SECONDS)).toBe(50);
  });

  it('clamps to 0 for a window that has not started yet', () => {
    const resetsAt = '2026-07-16T17:00:00.000Z';
    const nowMs = Date.parse('2026-07-16T11:00:00.000Z'); // an hour before the window starts
    expect(windowElapsedPct(nowMs, resetsAt, FIVE_H_SECONDS)).toBe(0);
  });

  it('clamps to 100 for a resets_at already in the past (a stale sample)', () => {
    const resetsAt = '2026-07-16T17:00:00.000Z';
    const nowMs = Date.parse('2026-07-16T18:00:00.000Z'); // an hour past reset
    expect(windowElapsedPct(nowMs, resetsAt, FIVE_H_SECONDS)).toBe(100);
  });

  it('reads 0 for an unparseable resets_at rather than throwing', () => {
    expect(windowElapsedPct(Date.now(), 'not-a-date', FIVE_H_SECONDS)).toBe(0);
  });
});

describe('classifySubscriptionFreshness', () => {
  it('reads fresh at exactly 15m', () => {
    expect(classifySubscriptionFreshness(FRESHNESS_AGING_AFTER_MS)).toBe('fresh');
  });

  it('reads aging just past 15m', () => {
    expect(classifySubscriptionFreshness(FRESHNESS_AGING_AFTER_MS + 1)).toBe('aging');
  });

  it('reads aging at exactly 60m', () => {
    expect(classifySubscriptionFreshness(FRESHNESS_STALE_AFTER_MS)).toBe('aging');
  });

  it('reads stale just past 60m', () => {
    expect(classifySubscriptionFreshness(FRESHNESS_STALE_AFTER_MS + 1)).toBe('stale');
  });
});

describe('toPaceBars', () => {
  it('folds each window to its label, utilization, and elapsed share, in wire order', () => {
    const nowMs = Date.parse('2026-07-16T12:00:00.000Z');
    expect(
      toPaceBars(nowMs, [
        { window: '5h', utilization_pct: 40, resets_at: '2026-07-16T14:30:00.000Z', window_seconds: 5 * 60 * 60 },
        { window: '7d', utilization_pct: 10, resets_at: 'not-a-date', window_seconds: 7 * 24 * 60 * 60 },
      ]),
    ).toEqual([
      { window: '5h', utilizationPct: 40, elapsedPct: 50 },
      { window: '7d', utilizationPct: 10, elapsedPct: 0 },
    ]);
  });
});
