import { defaultRoutineWindow } from './routine-window';

describe('defaultRoutineWindow', () => {
  it('cuts a 28-day window ending at now, with the introduced boundary at its start', () => {
    const now = Date.parse('2026-01-29T00:00:00Z');

    const window = defaultRoutineWindow(now);

    expect(window.until).toBe('2026-01-29T00:00:00.000Z');
    expect(window.since).toBe('2026-01-01T00:00:00.000Z');
    expect(window.introducedBoundary).toBe(window.since);
    expect(window.periodDays).toBe(7);
  });
});
