import { windowElapsedPct } from './runner-rows';

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
